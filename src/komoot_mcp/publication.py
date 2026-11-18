"""Durable Komoot publication journals independent of exporter workflow state."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

from . import komoot_import
from .artifacts import read_manifest, validate_prepared


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _create(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())


def _available(path):
    candidate = path
    number = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}-{number:02d}{path.suffix}")
        number += 1
    return candidate


def _receipt(value):
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1 or not isinstance(value.get("tours"), list):
        raise ValueError("Expected a Komoot import receipt version 1")
    for tour in value["tours"]:
        if (not isinstance(tour, dict) or type(tour.get("route_id")) not in (str, int)
                or not str(tour["route_id"]) or not tour.get("tour_id")):
            raise ValueError("Receipt tours require route_id and tour_id")
        identity = str(tour["tour_id"])
        if isinstance(tour["tour_id"], bool) or not identity.isdecimal() or int(identity) < 1:
            raise ValueError("Receipt tour IDs must be positive integers")
    return value


def _stage(value, receipt=False):
    stage = value.get("stage_id")
    if stage is None:
        # Legacy preparations predate stage_id. Their main route identity carries it.
        routes = value.get("tours" if receipt else "routes", [])
        if routes:
            route = routes[0]
            identity = route.get("route_id") if receipt else route.get("source_route_id", route.get("id"))
            match = re.fullmatch(r"([0-9]+)(?:-part-[0-9]+)?", str(identity))
            stage = int(match[1]) if match else None
    if isinstance(stage, str) and stage.isdecimal():
        stage = int(stage)
    if type(stage) is not int or stage < 1:
        raise ValueError("A positive stage_id is required for publication naming")
    return stage


class Publication:
    """Serialize one explicit publication directory and refuse uncertain retries.

    pending.json is retained after any failed remote operation. Reconcile it
    manually against Komoot before removing it; neither adoption nor update
    silently clears that protection. No exporter stage state is read or changed.
    """

    def __init__(self, directory):
        self.directory = Path(directory).expanduser().resolve()

    @contextmanager
    def _lock(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        lock = self.directory / ".lock"
        try:
            with lock.open("x", encoding="utf-8") as stream:
                stream.write(str(os.getpid()))
        except FileExistsError as exc:
            raise ValueError(f"Publication is locked: inspect {lock} before retrying") from exc
        try:
            yield
        finally:
            lock.unlink()

    def _receipts(self):
        receipts = []
        for path in sorted(self.directory.glob("*.json")):
            if path.name == "pending.json":
                continue
            value = _read(path)
            # Completed intent records have no tours and are not receipts.
            if isinstance(value, dict) and "tours" in value:
                receipts.append((path, _receipt(value)))
        return receipts

    def adopt_receipt(self, receipt_path, prepared_path=None):
        """Copy a receipt with stable identity, optionally rebinding explicit artifacts."""
        with self._lock():
            source = Path(receipt_path).expanduser().resolve()
            payload = source.read_bytes()
            receipt = _receipt(json.loads(payload))
            identity = receipt.get("receipt_id") or hashlib.sha256(payload).hexdigest()[:24]
            if prepared_path is not None:
                prepared = Path(prepared_path).expanduser().resolve()
                manifest = read_manifest(prepared)
                routes = {str(route["id"]) for route in manifest["routes"]}
                if any(str(tour["route_id"]) not in routes for tour in receipt["tours"]):
                    raise ValueError("Receipt routes do not match the supplied preparation")
                receipt.setdefault("original_prepared_path", receipt.get("prepared_path"))
                receipt["prepared_path"] = str(prepared)
                stage = _stage(manifest)
            else:
                stage = _stage(receipt, receipt=True)
            receipt["receipt_id"] = identity
            matches = [path for path, value in self._receipts() if value.get("receipt_id") == identity]
            if len(matches) > 1:
                raise ValueError("Historical receipt has duplicate stored identities")
            timestamp = receipt.get("imported_at") or receipt.get("created_at") or datetime.now(timezone.utc).isoformat()
            destination = matches[0] if matches else _available(self.directory / (
                f"{timestamp[:10]}-stage-{stage:02d}-komoot-importbeleg.json"))
            if matches:
                if _read(destination) != receipt:
                    raise ValueError("Historical receipt already stored with different bindings")
            else:
                _create(destination, receipt)
            return {"stage": stage, "receipt_path": str(destination), "tours": len(receipt["tours"])}

    def _preflight(self, prepared_path, update, *, locked=False):
        manifest, report = validate_prepared(prepared_path)
        report.update({"publication_directory": str(self.directory),
                       "action": "update" if update else "create",
                       "requested_visibility": "private", "scope": "local_preflight_only",
                       "live_komoot_verified": False,
                       "limitations": ["No authentication, uploads, or live Komoot checks were performed.",
                                       "Local readiness does not establish live update readiness."]})
        issues = report["issues"]

        def issue(code, message):
            issues.append({"code": code, "message": message})

        try:
            stage = _stage(manifest)
            report["stage"] = stage
        except ValueError as exc:
            issue("stage_identity", str(exc))
        if not locked and (self.directory / ".lock").exists():
            issue("publication_locked", f"Publication is locked: inspect {self.directory / '.lock'} before retrying")
        if (self.directory / "pending.json").exists():
            issue("pending_operation", f"Unresolved Komoot operation: inspect {self.directory / 'pending.json'}; no automatic retry")
        try:
            receipts = self._receipts()
        except (OSError, ValueError, TypeError) as exc:
            issue("invalid_receipt", f"Cannot inspect publication receipts: {exc}")
            receipts = []
        report["receipt_paths"] = [str(path) for path, _ in receipts]
        if receipts and not update:
            issue("existing_receipts", "Existing Komoot receipts found; use an explicit update, not another import")
        if update and not receipts:
            issue("missing_receipts", "No imported Komoot receipts available to update")
        if update:
            new_routes = {str(route["id"]): route for route in manifest["routes"]}
            seen_tours, seen_routes, seen_receipts = set(), set(), set()
            for path, receipt in receipts:
                receipt_identity = receipt.get("receipt_id")
                if receipt_identity is not None:
                    if not isinstance(receipt_identity, str) or not receipt_identity or receipt_identity in seen_receipts:
                        issue("duplicate_receipt", "Resolve duplicate or invalid historical receipt identities before updating")
                    else:
                        seen_receipts.add(receipt_identity)
                if not receipt["tours"]:
                    issue("empty_receipt", "An empty historical receipt cannot be updated")
                old_routes = {}
                try:
                    old_path = receipt.get("prepared_path")
                    if not isinstance(old_path, str) or not old_path:
                        raise ValueError("Receipt requires an old prepared_path")
                    previous, old_report = validate_prepared(old_path)
                    if old_report["issues"]:
                        raise ValueError("Old preparation fails artifact validation")
                    old_routes = {str(route["id"]): route for route in previous["routes"]}
                except (OSError, ValueError, TypeError) as exc:
                    issue("old_preparation", f"Cannot read old preparation for {path}: {exc}")
                for tour in receipt["tours"]:
                    identity, key = int(tour["tour_id"]), str(tour["route_id"])
                    if identity in seen_tours or key in seen_routes or tour.get("verification") != "private-planned-waypoints-verified":
                        issue("unverified_receipt", "Resolve duplicate or unverified historical tours before updating")
                    seen_tours.add(identity)
                    seen_routes.add(key)
                    if key not in new_routes or key not in old_routes:
                        issue("route_identity", f"Receipt route {key} is missing from the old or current preparation")
                        continue
                    old, new = old_routes[key], new_routes[key]
                    if (set(old["aliases"]) != set(new["aliases"]) or
                            str(old.get("source_route_id", old["id"])) != str(new.get("source_route_id", new["id"]))):
                        issue("poi_identity", f"Refresh changes source route or POI identities for route {key}")
            if set(new_routes) != seen_routes:
                issue("route_identity", "Current route identities must match imported receipt routes exactly")
        report["ready"] = not issues
        return manifest, report, receipts

    def preview(self, prepared_path, update=False):
        """Inspect local artifacts and publication blockers without acquiring a lock."""
        return self._preflight(prepared_path, update)[1]

    async def publish(self, prepared_path, client, update=False):
        with self._lock():
            intent = self.directory / "pending.json"
            prepared = Path(prepared_path).expanduser().resolve()
            _, report, receipts = self._preflight(prepared, update, locked=True)
            if report["issues"]:
                raise ValueError("; ".join(issue["message"] for issue in report["issues"]))
            stage = report["stage"]
            _create(intent, {"prepared_path": str(prepared), "update": update,
                             "receipts": [str(path) for path, _ in receipts]})
            if update:
                results = []
                for path, _ in receipts:
                    result = await komoot_import.update_prepared(str(path), str(prepared), client)
                    if result.get("error") or result.get("isError"):
                        raise ValueError(f"Komoot update failed; inspect {intent} before retrying")
                    results.append(result)
                result = {"updates": results}
            else:
                receipt = _available(self.directory / (
                    f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}-stage-{stage:02d}-komoot-importbeleg.json"))
                result = await komoot_import.import_prepared(str(prepared), str(receipt), client)
                if result.get("error") or result.get("isError"):
                    raise ValueError(f"Komoot import failed; inspect {intent} before retrying")
            action = "aktualisierung" if update else "import"
            completed = _available(self.directory / (
                f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}-stage-{stage:02d}-komoot-{action}-abgeschlossen.json"))
            intent.rename(completed)
            return result
