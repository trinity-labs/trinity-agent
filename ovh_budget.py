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


class BudgetExceeded(BudgetLedgerError):
    def __init__(self, status: dict, amount: Decimal):
        self.status = status
        self.amount = amount
        labels = {"daily": "quotidien", "weekly": "hebdomadaire", "monthly": "mensuel", "credit": "crédit temporaire"}
        exceeded = [
            f"{labels[name]} ({period['limit']:.2f} €, réinitialisation {period['reset_at']})"
            for name, period in status["periods"].items()
            if period["limit"] > 0 and name in status.get("exceeded_periods", [])
        ]
        super().__init__(
            f"Budget {' / '.join(exceeded)} insuffisant : "
            f"la requête nécessite une réservation de {amount} €."
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
            used = _total((spent, reserved))
            with localcontext() as context:
                context.prec = 80
                remaining = max(ZERO, limit - used) if limit else None
            periods[period] = {
                "limit": float(limit),
                "spent": float(spent),
                "reserved": float(reserved),
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
                spent = _money(data["spent"][period].get(key, "0"))
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
