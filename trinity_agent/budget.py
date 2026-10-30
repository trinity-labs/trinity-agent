"""Persistent EUR request budgets, shared by the local agent processes.

Periods are UTC calendar days, weeks starting Monday, and calendar months.
Open reservations count in every *current* period, including after a rollover.
Completed requests are charged once, on their settlement date. A request that
fails without trustworthy usage is charged its entire reservation. Reservations
never expire automatically: a crashed process may have incurred a provider bill.

Only period counters (90 days) and open reservations are retained. Reservation
IDs are UUIDv8 values containing a sequence and an authenticated ledger-specific
tag. This makes repeated settlement a no-op without retaining per-request logs.
Limits are local estimates, not provider-enforced billing quotas.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import tempfile
from typing import Callable, Iterator
import uuid


PERIODS = ("daily", "weekly", "monthly")
ZERO = Decimal("0")
UTC = timezone.utc


class BudgetLedgerError(RuntimeError):
    """The ledger cannot be trusted or updated; callers must not send a request."""


class AvailableBalanceLedger:
    """Reserve against a verified balance and fail closed when it is unavailable."""

    def __init__(self, path: Path, provider: str, currency: str, balance: Callable[[], dict]):
        self.path, self.provider = Path(path), provider
        self.currency, self.balance = currency.upper(), balance

    def _snapshot(self):
        try:
            value = self.balance()
            if (not isinstance(value, dict) or not value.get('available') or value.get('stale')
                    or value.get('balance_error') or str(value.get('currency', '')).upper() != self.currency):
                raise ValueError('solde absent ou périmé')
            raw_remaining = value['credit_remaining'] if 'credit_remaining' in value else value['credit_available']
            remaining = _money(raw_remaining)
            if value.get('account_available') is False:
                remaining = ZERO
            return remaining, str(value.get('last_update') or value.get('cached_at') or '')
        except (KeyError, TypeError, ValueError, InvalidOperation) as error:
            raise BudgetLedgerError(
                f'Budget {self.provider} : solde disponible vérifié indisponible ; appel bloqué.') from error

    def _load(self):
        try:
            data = json.loads(self.path.read_text(), object_pairs_hook=_unique_object,
                              parse_constant=_reject_constant)
        except FileNotFoundError:
            return {'version': 1, 'provider': self.provider, 'currency': self.currency,
                    'namespace': uuid.uuid4().hex, 'sequence': 0, 'reported': None,
                    'pending_spent': '0', 'reservations': {}}
        try:
            if (data.get('version') != 1 or data.get('provider') != self.provider
                    or data.get('currency') != self.currency
                    or len(bytes.fromhex(data['namespace'])) != 16
                    or type(data['sequence']) is not int or not 0 <= data['sequence'] < 2**48
                    or not isinstance(data['reservations'], dict)):
                raise ValueError
            _money(data['pending_spent'])
            if data['reported'] is not None:
                _money(data['reported']['remaining'])
            for token, item in data['reservations'].items():
                if not BudgetLedger._valid_id(token, data):
                    raise ValueError
                _money(item['amount'])
            return data
        except (KeyError, TypeError, ValueError) as error:
            raise BudgetLedgerError(f'Registre du solde {self.provider} invalide ; appels bloqués.') from error

    @contextmanager
    def _transaction(self, refresh=True):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.path.with_name(self.path.name + '.lock'), os.O_CREAT | os.O_RDWR, 0o600)
            with os.fdopen(descriptor, 'r+') as lock:
                os.fchmod(lock.fileno(), 0o600)
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                data = self._load()
                if refresh:
                    remaining, observed_at = self._snapshot()
                    old = data.get('reported')
                    if old:
                        reflected = max(ZERO, _difference(_money(old['remaining']), remaining))
                        data['pending_spent'] = str(max(ZERO, _difference(
                            _money(data['pending_spent']), reflected)))
                    data['reported'] = {'remaining': str(remaining), 'observed_at': observed_at}
                yield data
                serialized = json.dumps(data, sort_keys=True, separators=(',', ':'), allow_nan=False)
                temporary = self.path.with_name('.' + self.path.name + '.' + uuid.uuid4().hex)
                temporary.write_text(serialized, encoding='utf-8')
                os.chmod(temporary, 0o600)
                os.replace(temporary, self.path)
        except BudgetLedgerError:
            raise
        except OSError as error:
            raise BudgetLedgerError(f'Registre du solde {self.provider} inaccessible ; appels bloqués.') from error

    def _status(self, data):
        reported = _money(data['reported']['remaining']) if data.get('reported') else ZERO
        pending = _money(data['pending_spent'])
        reserved = _total(_money(item['amount']) for item in data['reservations'].values())
        available = max(ZERO, _difference(reported, _total((pending, reserved))))
        return {'provider': self.provider, 'currency': self.currency, 'enforced': True,
                'blocked': available <= ZERO, 'provider_balance': float(reported),
                'pending_spent': float(pending), 'reserved': float(reserved),
                'available_to_reserve': float(available),
                'pending_requests': len(data['reservations']), 'periods': {}}

    def status(self):
        with self._transaction() as data:
            return self._status(data)

    def reserve(self, amount, metadata=None):
        amount = _money(amount)
        with self._transaction() as data:
            status = self._status(data)
            if amount > _money(status['available_to_reserve']):
                status.update(blocked=True, exceeded_periods=['provider'], requested=float(amount))
                raise BudgetExceeded(status, amount)
            data['sequence'] += 1
            token = BudgetLedger._reservation_id(data['sequence'], data['namespace'])
            data['reservations'][token] = {'amount': str(amount), 'metadata': metadata or {}}
            return token

    def settle(self, reservation_id, actual_amount):
        actual = _money(actual_amount) if actual_amount is not None else None
        with self._transaction(refresh=False) as data:
            if not BudgetLedger._valid_id(reservation_id, data):
                raise BudgetLedgerError('Réservation de solde inconnue ; appels bloqués.')
            reservation = data['reservations'].pop(reservation_id, None)
            if reservation is not None:
                charge = _money(reservation['amount']) if actual is None else actual
                data['pending_spent'] = str(_total((_money(data['pending_spent']), charge)))
            return self._status(data)


class BudgetExceeded(BudgetLedgerError):
    def __init__(self, status: dict, amount: Decimal):
        self.status = status
        self.amount = amount
        labels = {"daily": "quotidien", "weekly": "hebdomadaire", "monthly": "mensuel", "credit": "crédit temporaire", "provider": "solde fournisseur"}
        exceeded = [
            f"{labels[name]} ({period['limit']:.2f} €, réinitialisation {period['reset_at']})"
            for name, period in status["periods"].items()
            if (period["limit"] > 0 or name == "provider") and name in status.get("exceeded_periods", [])
        ]
        if 'provider' in status.get('exceeded_periods', []) and not exceeded:
            exceeded = ['solde fournisseur disponible']
        currency = status.get('currency', 'EUR')
        super().__init__(
            f"Budget {' / '.join(exceeded)} insuffisant : "
            f"la requête nécessite une réservation de {amount} {currency}."
        )


def _money(value: object) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("Un montant doit être un nombre positif ou nul.")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise ValueError("Montant monétaire invalide.") from error
    if not amount.is_finite() or amount < ZERO:
        raise ValueError("Un montant doit être fini, positif ou nul.")
    # Bound pathological decimal representations while preserving sub-cent costs.
    if amount and (amount.adjusted() > 18 or amount.as_tuple().exponent < -36):
        raise ValueError("Précision ou grandeur du montant non prise en charge.")
    if len(amount.as_tuple().digits) > 56:
        raise ValueError("Précision du montant non prise en charge.")
    return amount


def _total(amounts) -> Decimal:
    with localcontext() as context:
        context.prec = 80
        return sum(amounts, ZERO)


def _difference(left, right) -> Decimal:
    with localcontext() as context:
        context.prec = 80
        return left - right


def _unique_object(pairs) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Clé dupliquée dans le registre.")
        result[key] = value
    return result


def _reject_constant(value: str):
    raise ValueError(f"Constante JSON invalide : {value}")


def _periods(now: datetime) -> dict[str, tuple[str, datetime]]:
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week = day - timedelta(days=day.weekday())
    month = day.replace(day=1)
    next_month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    return {
        "daily": (day.date().isoformat(), day + timedelta(days=1)),
        "weekly": (week.date().isoformat(), week + timedelta(days=7)),
        "monthly": (month.date().isoformat(), next_month),
    }


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


class BudgetLedger:
    currency = 'EUR'

    def __init__(self, path: Path, limits: dict, now: Callable[[], datetime] | None = None, credit: dict | None = None):
        self.path = Path(path)
        unknown = set(limits) - set(PERIODS)
        if unknown:
            raise ValueError(f"Période de budget inconnue : {', '.join(sorted(unknown))}")
        self.limits = {period: _money(limits.get(period, 0)) for period in PERIODS}
        self.now = now or (lambda: datetime.now(UTC))
        self.credit = None
        if credit is not None:
            try:
                start = datetime.fromisoformat(credit['starts_at'])
                end = datetime.fromisoformat(credit['expires_at'])
                amount = _money(credit['limit_eur'])
                if (start.tzinfo is None or end.tzinfo is None or not amount
                        or start.utcoffset() != timedelta(0)
                        or start != start.replace(hour=0, minute=0, second=0, microsecond=0)
                        or not timedelta(0) < end - start <= timedelta(days=89)):
                    raise ValueError('Fenêtre du crédit invalide')
                self.credit = (start, end.astimezone(UTC), amount)
            except (KeyError, TypeError, ValueError) as error:
                raise BudgetLedgerError('Politique de crédit invalide : appels bloqués.') from error

    @classmethod
    def from_policy(cls, path, limits, policy_path, now=None):
        """Reload local overrides per request, including in already-running agents."""
        try:
            policy = json.loads(Path(policy_path).read_text(), object_pairs_hook=_unique_object,
                                parse_constant=_reject_constant)
        except FileNotFoundError:
            return cls(path, limits, now=now)
        except (OSError, ValueError) as error:
            raise BudgetLedgerError('Politique de budget illisible : appels bloqués.') from error
        try:
            if policy['schema'] != 1 or set(policy['limits']) - set(PERIODS):
                raise ValueError('Politique inconnue')
            return cls(path, {**limits, **policy['limits']}, now=now, credit=policy.get('credit'))
        except (KeyError, TypeError, ValueError) as error:
            raise BudgetLedgerError('Politique de budget invalide : appels bloqués.') from error

    def _credit_status(self, data, now, reserved):
        start, end, limit = self.credit
        spent = _total(_money(amount) for day, amount in data['spent']['daily'].items()
                       if start.date().isoformat() <= day <= now.date().isoformat())
        active = start <= now < end
        remaining = max(ZERO, limit - spent - reserved) if active else ZERO
        return {'limit': float(limit), 'spent': float(spent), 'reserved': float(reserved),
                'remaining': float(remaining), 'reset_at': _iso(end), 'expires_at': _iso(end),
                'active': active}

    @staticmethod
    def _reservation_id(sequence: int, namespace: str) -> str:
        digest = hmac.new(bytes.fromhex(namespace), str(sequence).encode("ascii"), hashlib.sha256).hexdigest()
        raw = f"{sequence:012x}8{digest[:3]}{8 + (int(digest[3], 16) & 3):x}{digest[4:19]}"
        return str(uuid.UUID(hex=raw))

    @classmethod
    def _valid_id(cls, token: object, data: dict) -> bool:
        try:
            parsed = uuid.UUID(str(token))
            sequence = int(parsed.hex[:12], 16)
            return (
                isinstance(token, str)
                and 0 < sequence <= data["sequence"]
                and hmac.compare_digest(token, cls._reservation_id(sequence, data["namespace"]))
            )
        except (ValueError, TypeError, AttributeError):
            return False

    def _load(self) -> dict:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {
                "version": 1,
                "currency": "EUR",
                "namespace": uuid.uuid4().hex,
                "sequence": 0,
                "spent": {period: {} for period in PERIODS},
                "reservations": {},
            }
        try:
            data = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
            if not isinstance(data, dict) or data.get("version") != 1 or data.get("currency") != "EUR":
                raise ValueError("Format de registre inconnu.")
            if not isinstance(data["namespace"], str) or len(bytes.fromhex(data["namespace"])) != 16:
                raise ValueError("Identifiant de registre invalide.")
            if type(data["sequence"]) is not int or not 0 <= data["sequence"] < 2**48:
                raise ValueError("Compteur de registre invalide.")
            if not isinstance(data["spent"], dict) or set(data["spent"]) != set(PERIODS):
                raise ValueError("Compteurs de périodes absents.")
            for period, counters in data["spent"].items():
                if not isinstance(counters, dict):
                    raise ValueError("Compteurs de périodes invalides.")
                for key, amount in counters.items():
                    start = datetime.strptime(key, "%Y-%m-%d").replace(tzinfo=UTC)
                    if _periods(start)[period][0] != key:
                        raise ValueError("Date de période invalide.")
                    _money(amount)
            if not isinstance(data["reservations"], dict):
                raise ValueError("Réservations invalides.")
            for token, reservation in data["reservations"].items():
                if not self._valid_id(token, data) or not isinstance(reservation, dict):
                    raise ValueError("Identifiant de réservation invalide.")
                _money(reservation["amount"])
                created = datetime.fromisoformat(reservation["created_at"])
                if created.tzinfo is None or created.utcoffset() != timedelta(0):
                    raise ValueError("Date de réservation invalide.")
                if not isinstance(reservation.get("metadata"), dict):
                    raise ValueError("Métadonnées de réservation invalides.")
            return data
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise BudgetLedgerError(
                f"Registre de budgets invalide ({self.path.name}) : appels bloqués. "
                "Restaurez le registre ; il n'a pas été remis à zéro."
            ) from error

    def _write(self, serialized: str) -> None:
        temporary = None
        try:
            descriptor, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            temporary = None
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass

    @contextmanager
    def _transaction(self) -> Iterator[tuple[dict, datetime]]:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Lock the separate stable inode, never the atomically replaced JSON.
            lock_path = self.path.with_name(self.path.name + ".lock")
            descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            with os.fdopen(descriptor, "r+") as lock:
                os.fchmod(lock.fileno(), 0o600)
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                now = self.now()
                if not isinstance(now, datetime) or now.tzinfo is None:
                    raise ValueError("L'horloge des budgets doit fournir une date avec fuseau horaire.")
                now = now.astimezone(UTC)
                existed = self.path.exists()
                data = self._load()
                before = json.dumps(data, sort_keys=True, separators=(",", ":"))
                cutoff = (now - timedelta(days=90)).date().isoformat()
                for counters in data["spent"].values():
                    for key in list(counters):
                        if key < cutoff:
                            del counters[key]
                yield data, now
                after = json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False)
                if not existed or after != before:
                    self._write(after)
        except BudgetLedgerError:
            raise
        except (OSError, UnicodeError) as error:
            raise BudgetLedgerError(
                f"Registre de budgets inaccessible ({self.path.name}) : appels bloqués."
            ) from error

    def _status(self, data: dict, now: datetime) -> dict:
        reserved = _total(_money(item["amount"]) for item in data["reservations"].values())
        periods = {}
        blocked = False
        for period, (key, reset) in _periods(now).items():
            limit = self.limits[period]
            spent = _money(data["spent"][period].get(key, "0"))
            accounted = self._accounted_spent(data, period, key)
            used = _total((accounted, reserved))
            with localcontext() as context:
                context.prec = 80
                remaining = max(ZERO, limit - used) if limit else None
            periods[period] = {
                "limit": float(limit),
                "spent": float(spent),
                "reserved": float(reserved),
                "accounted_spent": float(accounted),
                "remaining": float(remaining) if remaining is not None else None,
                "reset_at": _iso(reset),
            }
            blocked = blocked or bool(limit and used >= limit)
        if self.credit:
            periods['credit'] = self._credit_status(data, now, reserved)
            blocked = blocked or periods['credit']['remaining'] <= 0
        return {
            "currency": "EUR",
            "periods": periods,
            "blocked": blocked,
            "pending_requests": len(data["reservations"]),
            "updated_at": _iso(now),
        }

    def _accounted_spent(self, data, period, key):
        return _money(data["spent"][period].get(key, "0"))

    def status(self) -> dict:
        with self._transaction() as (data, now):
            return self._status(data, now)

    def reserve(self, amount, metadata: dict | None = None) -> str:
        amount = _money(amount)
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            raise ValueError("Les métadonnées de réservation doivent être un objet JSON.")
        encoded_metadata = json.dumps(metadata, ensure_ascii=False, allow_nan=False)
        if len(encoded_metadata.encode("utf-8")) > 4096:
            raise ValueError("Métadonnées de réservation trop volumineuses.")
        with self._transaction() as (data, now):
            reserved = _total(_money(item["amount"]) for item in data["reservations"].values())
            exceeded = []
            for period, (key, _reset) in _periods(now).items():
                limit = self.limits[period]
                spent = self._accounted_spent(data, period, key)
                if amount and limit and _total((spent, reserved, amount)) > limit:
                    exceeded.append(period)
            if self.credit and amount:
                credit = self._credit_status(data, now, reserved)
                start, end, limit = self.credit
                spent = _total(_money(value) for day, value in data['spent']['daily'].items()
                               if start.date().isoformat() <= day <= now.date().isoformat())
                if not credit['active'] or _total((spent, reserved, amount)) > limit:
                    exceeded.append('credit')
            if exceeded:
                status = self._status(data, now)
                status["blocked"] = True
                status["exceeded_periods"] = exceeded
                status["requested"] = float(amount)
                raise BudgetExceeded(status, amount)
            if data["sequence"] >= 2**48 - 1:
                raise BudgetLedgerError("Nombre maximal de réservations atteint : appels bloqués.")
            data["sequence"] += 1
            token = self._reservation_id(data["sequence"], data["namespace"])
            data["reservations"][token] = {
                "amount": str(amount),
                "created_at": _iso(now),
                "metadata": json.loads(encoded_metadata),
            }
            return token

    def settle(self, reservation_id: str, actual_amount) -> dict:
        actual = _money(actual_amount) if actual_amount is not None else None
        with self._transaction() as (data, now):
            if not self._valid_id(reservation_id, data):
                raise BudgetLedgerError("Réservation inconnue : consommation non imputée, appels à bloquer.")
            reservation = data["reservations"].get(reservation_id)
            if reservation is None:
                return self._status(data, now)
            charge = _money(reservation["amount"]) if actual is None else actual
            for period, (key, _reset) in _periods(now).items():
                previous = _money(data["spent"][period].get(key, "0"))
                data["spent"][period][key] = str(_total((previous, charge)))
            del data["reservations"][reservation_id]
            return self._status(data, now)


class ProviderBalanceLedger(BudgetLedger):
    """Enforce the greater of OVH observations and durable local accounting.

    Daily/weekly counters measure observed changes since their first sample,
    not a reconstructed invoice. Each local period opens at its first observed
    provider amount, then adds measured request costs or the reserved maximum
    for uncertain calls. Taking max(local, provider) avoids both delayed-report
    bypasses and normal double charging, without guessing request attribution.
    """
    @classmethod
    def from_provider(cls, path, limits, policy_path, provider, now=None):
        ledger = cls.from_policy(path, limits, policy_path, now=now)
        ledger.provider = provider
        ledger.credit_policy = ledger.credit
        ledger.credit = None
        return ledger

    def _load(self):
        existed = self.path.exists()
        data = super()._load()
        if not existed:
            return data
        try:
            if 'provider' not in data and 'ovh' in data:
                data['provider'] = data.pop('ovh')
            observation = data['provider']
            for key in ('usage', 'credit', 'total'):
                _money(observation[key])
            for key in ('reported_at', 'fetched_at'):
                if datetime.fromisoformat(observation[key].replace('Z', '+00:00')).tzinfo is None:
                    raise ValueError('Date OVH invalide')
            if not isinstance(observation['project'], str) or not observation['project']:
                raise ValueError('Projet OVH absent')
            datetime.strptime(observation['month'], '%Y-%m-%d')
            for period in ('daily', 'weekly'):
                baseline = observation['baselines'][period]
                _money(baseline['total'])
                date = datetime.strptime(baseline['key'], '%Y-%m-%d').replace(tzinfo=UTC)
                if _periods(date)[period][0] != baseline['key']:
                    raise ValueError('Période OVH invalide')
                if datetime.fromisoformat(baseline['since'].replace('Z', '+00:00')).tzinfo is None:
                    raise ValueError('Début de suivi absent')
                if type(baseline['partial']) is not bool:
                    raise ValueError('Couverture du suivi invalide')
            if 'local' in observation:
                local = observation['local']
                if datetime.fromisoformat(local['started_at'].replace('Z', '+00:00')).tzinfo is None:
                    raise ValueError('Date du suivi local invalide')
                for field in ('spent', 'opening', 'provider_adjustment'):
                    if not isinstance(local[field], dict) or set(local[field]) != set(PERIODS):
                        raise ValueError('Périodes du suivi local invalides')
                    for period, counters in local[field].items():
                        if not isinstance(counters, dict):
                            raise ValueError('Compteurs locaux invalides')
                        for key, value in counters.items():
                            date = datetime.strptime(key, '%Y-%m-%d').replace(tzinfo=UTC)
                            if _periods(date)[period][0] != key:
                                raise ValueError('Date du compteur local invalide')
                            _money(value)
                for period in PERIODS:
                    if (set(local['spent'][period]) != set(local['opening'][period])
                            or set(local['spent'][period]) != set(local['provider_adjustment'][period])):
                        raise ValueError('Base d’ouverture locale absente')
                    for key, value in local['spent'][period].items():
                        if _money(value) < _total((_money(local['opening'][period][key]),
                                                  _money(local['provider_adjustment'][period][key]))):
                            raise ValueError('Compteur local inférieur à sa base')
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise BudgetLedgerError('Registre des observations OVH invalide ; aucun compteur remis à zéro.') from error
        return data

    def _snapshot(self):
        snapshot = self.provider()
        try:
            if not snapshot.get('available') or snapshot.get('stale') or snapshot.get('balance_error'):
                raise ValueError('Relevé indisponible ou périmé')
            if snapshot.get('currency') != 'EUR' or snapshot.get('credit_currency') != 'EUR':
                raise ValueError('Devise du relevé incompatible')
            usage = _money(snapshot['current_usage'])
            credit = _money(snapshot['credit_available'])
            reported = datetime.fromisoformat(snapshot['last_update'].replace('Z', '+00:00'))
            period = datetime.fromisoformat(snapshot['usage_period']['from'].replace('Z', '+00:00'))
            cached = datetime.fromtimestamp(float(snapshot['cached_at']), UTC)
            now = self.now().astimezone(UTC)
            if reported.tzinfo is None or period.tzinfo is None or not snapshot.get('project'):
                raise ValueError('Période du relevé inconnue')
            if not timedelta(0) <= now - cached <= timedelta(minutes=5):
                raise ValueError('Cache de facturation trop ancien')
            if not timedelta(0) <= now - reported <= timedelta(hours=24):
                raise ValueError('Consommation OVH trop ancienne')
            if period.astimezone(UTC).date().isoformat() != _periods(now)['monthly'][0]:
                raise ValueError('Relevé du mois courant indisponible')
            return dict(project=snapshot['project'], usage=str(usage), credit=str(credit),
                        reported_at=_iso(reported.astimezone(UTC)), fetched_at=_iso(cached), month=period.date().isoformat())
        except (KeyError, TypeError, ValueError, OverflowError) as error:
            raise BudgetLedgerError('Budget OVH : solde vérifié indisponible ; configurez ou actualisez le suivi OVH.') from error

    def _observe(self, data, now, snapshot):
        old = data.get('provider')
        if old and old['project'] != snapshot['project']:
            raise BudgetLedgerError('Le projet de facturation OVH a changé ; registre distinct requis.')
        # A concurrent backend may still hold a valid but older cache.
        if old and (snapshot['reported_at'], snapshot['fetched_at']) < (old['reported_at'], old['fetched_at']):
            snapshot = {key: old[key] for key in ('project','usage','credit','reported_at','fetched_at','month')}
        usage = _money(snapshot['usage'])
        previous_total = _money(old['total']) if old else usage
        delta = _difference(usage, _money(old['usage'])) if old and old['month'] == snapshot['month'] else usage if old else ZERO
        total = max(ZERO, _total((previous_total, delta)))
        baselines = old.get('baselines', {}) if old else {}
        for period, (key, _) in _periods(now).items():
            if period == 'monthly':
                data['spent'][period][key] = str(usage)
                continue
            baseline = baselines.get(period)
            if not baseline or baseline['key'] != key:
                baseline = {'key': key, 'total': str(previous_total), 'since': _iso(now), 'partial': True}
                baselines[period] = baseline
            data['spent'][period][key] = str(max(ZERO, _difference(total, _money(baseline['total']))))
        local = {'local': old['local']} if old and 'local' in old else {}
        data['provider'] = {**snapshot, 'total': str(total), 'baselines': baselines, **local}
        self._ensure_local(data, now)

    def _ensure_local(self, data, now):
        local = data['provider'].setdefault('local', {
            'started_at': _iso(now), 'spent': {period: {} for period in PERIODS},
            'opening': {period: {} for period in PERIODS},
            'provider_adjustment': {period: {} for period in PERIODS}})
        cutoff = (now - timedelta(days=90)).date().isoformat()
        for period, (key, _) in _periods(now).items():
            for field in ('spent', 'opening', 'provider_adjustment'):
                for stale in list(local[field][period]):
                    if stale < cutoff:
                        del local[field][period][stale]
            if key not in local['spent'][period]:
                opening = data['spent'][period].get(key, '0')
                local['spent'][period][key] = opening
                local['opening'][period][key] = opening
                local['provider_adjustment'][period][key] = '0'
            provider = _money(data['spent'][period].get(key, '0'))
            recorded = _money(local['spent'][period][key])
            if provider > recorded:
                # A provider jump above local accounting must raise the floor
                # before later requests add costs; otherwise max() leaves a gap
                # in which new, unreported spending would not consume the limit.
                adjustment = _difference(provider, recorded)
                local['spent'][period][key] = str(provider)
                local['provider_adjustment'][period][key] = str(_total((
                    _money(local['provider_adjustment'][period][key]), adjustment)))
        return local

    @staticmethod
    def _local_requests(local, period, key):
        return _difference(_money(local['spent'][period].get(key, '0')),
            _total((_money(local['opening'][period].get(key, '0')),
                    _money(local['provider_adjustment'][period].get(key, '0')))))

    def _accounted_spent(self, data, period, key):
        provider = super()._accounted_spent(data, period, key)
        local = data.get('provider', {}).get('local', {}).get('spent', {}).get(period, {}).get(key, '0')
        return max(provider, _money(local))

    def _available(self, data, now):
        observation = data['provider']
        monthly = self._accounted_spent(data, 'monthly', _periods(now)['monthly'][0])
        available = max(ZERO, _difference(_money(observation['credit']), monthly))
        if self.credit_policy:
            start, end, cap = self.credit_policy
            local = observation['local']
            spent = _total(self._local_requests(local, 'daily', day) for day in local['spent']['daily']
                           if start.date().isoformat() <= day <= now.date().isoformat())
            available = min(available, max(ZERO, _difference(cap, spent))) if start <= now < end else ZERO
        return available

    def _status(self, data, now):
        result = super()._status(data, now)
        observation = data['provider']
        reserved = _total(_money(item['amount']) for item in data['reservations'].values())
        available = self._available(data, now)
        local = observation.get('local', {})
        for period, (key, _) in _periods(now).items():
            result['periods'][period].update(
                local_spent=float(self._local_requests(local, period, key)),
                local_opening=float(_money(local['opening'][period].get(key, '0'))),
                provider_adjustment=float(_money(local['provider_adjustment'][period].get(key, '0'))))
        result['periods']['provider'] = {
            'limit': float(_money(observation['credit'])), 'spent': float(_money(observation['usage'])),
            'reserved': float(reserved), 'remaining': float(max(ZERO, _difference(available, reserved))),
            'accounted_spent': result['periods']['monthly']['accounted_spent'],
            'local_spent': result['periods']['monthly']['local_spent'],
            'reset_at': _iso(self.credit_policy[1]) if self.credit_policy else _iso(_periods(now)['monthly'][1]),
        }
        for period in ('daily', 'weekly'):
            result['periods'][period].update(measurement='observed_provider_variation',
                                            **{key:observation['baselines'][period][key] for key in ('since','partial')})
        result.update(source='OVHcloud Public Cloud API', provider_reported_at=observation['reported_at'],
                      provider_balance=float(max(ZERO, _difference(_money(observation['credit']), _money(observation['usage'])))),
                      authorized_balance=float(available), reporting_delayed=True,
                      accounting_policy='max_local_provider',
                      local_accounting_started_at=local.get('started_at'),
                      local_accounting_scope='opening_and_provider_floor_plus_requests_since_start')
        result['blocked'] = result['blocked'] or available <= reserved
        return result

    @contextmanager
    def _transaction(self):
        snapshot = self._snapshot()
        with super()._transaction() as (data, now):
            self._observe(data, now, snapshot)
            sequence = data['sequence']
            yield data, now
            if data['sequence'] != sequence:
                reserved = _total(_money(item['amount']) for item in data['reservations'].values())
                if reserved > self._available(data, now):
                    status = self._status(data, now)
                    status.update(blocked=True, exceeded_periods=['provider'])
                    raise BudgetExceeded(status, reserved)

    def settle(self, reservation_id, actual_amount):
        # The provider-reported spending remains untouched. Charge the local
        # counters, using the reservation maximum when usage is unknown.
        # This transaction needs no provider request, including during an outage.
        actual = _money(actual_amount) if actual_amount is not None else None
        with super()._transaction() as (data, now):
            if not self._valid_id(reservation_id, data):
                raise BudgetLedgerError('Réservation inconnue.')
            if not data.get('provider'):
                raise BudgetLedgerError('Relevé OVH absent du registre.')
            local = self._ensure_local(data, now)
            reservation = data['reservations'].pop(reservation_id, None)
            if reservation is not None:
                charge = _money(reservation['amount']) if actual is None else actual
                for period, (key, _) in _periods(now).items():
                    local['spent'][period][key] = str(_total((_money(local['spent'][period][key]), charge)))
            return self._status(data, now)
