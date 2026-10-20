"""Budget enforcement immediately around every model request, including streams."""
from contextlib import asynccontextmanager
from decimal import Decimal, InvalidOperation
from pydantic_ai.models.wrapper import WrapperModel


class BudgetedModel(WrapperModel):
    def __init__(self, model, ledger, catalog_card, publish):
        super().__init__(model)
        self.ledger_factory = ledger
        self.catalog_card = catalog_card
        self.publish = publish

    def begin(self, settings):
        card = self.catalog_card(self.model_name)
        try:
            currency = str(card['currency']).upper()
            incoming = Decimal(str(card['input_per_million'])) / 1000000
            outgoing = Decimal(str(card['output_per_million'])) / 1000000
            window = int(card['context_length'])
            output = int((settings or {})['max_tokens'])
            if currency != 'EUR' or window <= 0 or not 0 < output <= window:
                raise ValueError('Paramètres de coût inconnus')
            if not all(p.is_finite() and p >= 0 for p in (incoming, outgoing)):
                raise ValueError('Tarifs invalides')
            if not (incoming or outgoing) and not card.get('free'):
                raise ValueError('Tarif absent, modèle non déclaré gratuit')
        except (KeyError, TypeError, ValueError, InvalidOperation) as error:
            raise ValueError('Budget : tarif ou limite du modèle inconnu ; appel bloqué.') from error
        # Upper bound for any accepted request within the advertised model window.
        # It does not rely on the approximate context tokenizer or image estimator.
        maximum = window * incoming + output * max(Decimal(0), outgoing - incoming)
        ledger = self.ledger_factory()
        try:
            reservation = ledger.reserve(maximum, metadata={
                'model': self.model_name, 'currency': currency,
                'pricing_source': card.get('pricing_source', ''),
            })
        except Exception as error:
            if hasattr(error, 'status'):
                self.publish(error.status)
            raise
        try:
            self.publish(ledger.status())
        except BaseException:
            # No model request has started. A failed status refresh or UI
            # callback must not leave a permanent charge/reservation behind.
            ledger.settle(reservation, Decimal(0))
            raise
        return ledger, reservation, incoming, outgoing, maximum

    def finish(self, reservation, response=None):
        ledger, identifier, incoming, outgoing, maximum = reservation
        amount = None
        if response is not None:
            usage = response.usage
            # Missing usage is not evidence that a paid request was free.
            if maximum == 0 or usage.input_tokens > 0 or usage.output_tokens > 0:
                amount = Decimal(usage.input_tokens) * incoming + Decimal(usage.output_tokens) * outgoing
        self.publish(ledger.settle(identifier, amount))

    async def request(self, messages, model_settings, model_request_parameters):
        reservation = self.begin(model_settings)
        response = None
        try:
            response = await self.wrapped.request(messages, model_settings, model_request_parameters)
            return response
        finally:
            self.finish(reservation, response)

    @asynccontextmanager
    async def request_stream(self, messages, model_settings, model_request_parameters, run_context=None):
        reservation = self.begin(model_settings)
        response = None
        try:
            async with self.wrapped.request_stream(messages, model_settings, model_request_parameters, run_context) as stream:
                yield stream
                response = stream.get()
        finally:
            self.finish(reservation, response)
