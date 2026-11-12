"""Drop-in body for neurons/miner.py::Miner.process_task (genomesio/subnet-niome @0c03fef).

The validator sends a GenomicsTaskSynapse carrying task.contract_url, task.hbb_ref_url and a
presigned PUT url. We download the two inputs, build a submission on CPU (<1s), and PUT it well
inside the 300s window. Fully self-contained, fault-tolerant, one submission per task.

Safety/pilot features:
  * Every real task's raw contract + reference are saved under CAPTURE_DIR BEFORE building, so the
    live schema can be verified against the offline reconstruction even if the build later fails.
  * Schema is validated; on mismatch the miner logs exactly what is missing instead of crashing.
Nothing here touches wallets or chain state."""
import json, logging, os, time, urllib.request
import httpx
from miner.solve import solve

logger = logging.getLogger(__name__)
CHR11_FA = os.environ.get("CHR11_FA", os.path.expanduser("~/niome-data/chr11.fa"))
CAPTURE_DIR = os.environ.get("NIOME_CAPTURE_DIR", os.path.expanduser("~/niome-data/captures"))
HTTP_TIMEOUT = 60
MAX_TRIES = 3

_REQUIRED_CONTRACT = ["active_mutations", "mutation_weights", "mutation_regions", "rules"]
_REQUIRED_RULES = ["base_padding", "max_experiments"]
_REQUIRED_REF = ["gene_region", "mutation_map"]


def _download_json(url: str) -> dict:
    last = None
    for attempt in range(1, MAX_TRIES + 1):
        try:
            with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as r:
                return json.loads(r.read())
        except Exception as e:
            last = e; time.sleep(2 ** attempt)
    raise last


def _capture(contract: dict, reference: dict) -> None:
    try:
        os.makedirs(CAPTURE_DIR, exist_ok=True)
        ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        json.dump(contract, open(os.path.join(CAPTURE_DIR, f"contract_{ts}.json"), "w"), indent=1)
        json.dump(reference, open(os.path.join(CAPTURE_DIR, f"reference_{ts}.json"), "w"), indent=1)
        logger.info(f"captured live contract+reference to {CAPTURE_DIR} ({ts})")
    except Exception as e:
        logger.warning(f"could not capture task inputs: {e}")


def _validate(contract: dict, reference: dict) -> list:
    missing = [f"contract.{k}" for k in _REQUIRED_CONTRACT if k not in contract]
    missing += [f"contract.rules.{k}" for k in _REQUIRED_RULES if k not in contract.get("rules", {})]
    missing += [f"reference.{k}" for k in _REQUIRED_REF if k not in reference]
    return missing


# Storage hosts the real NIOME backend hands out presigned URLs for.
TRUSTED_UPLOAD_HOSTS = ("amazonaws.com", "googleapis.com", "genomes.io", "storage.cloud.google.com")


def _host(url: str) -> str:
    from urllib.parse import urlparse
    try:
        return (urlparse(url).hostname or "?").lower()
    except Exception:
        return "?"


def _check_origin(contract_url: str, hbb_ref_url: str, presigned_url: str) -> bool:
    """Log where the task came from and where our submission would go.

    The upload target is what matters: if it is not legitimate backend storage, somebody is
    harvesting our work. Returns False when the upload host is untrusted AND
    NIOME_STRICT_UPLOAD_HOST=1 is set (default: warn loudly but proceed).
    """
    ch, rh, uh = _host(contract_url), _host(hbb_ref_url), _host(presigned_url)
    logging.info(f"task origin: contract={ch} reference={rh} | UPLOAD TARGET={uh}")
    trusted = any(uh == h or uh.endswith("." + h) for h in TRUSTED_UPLOAD_HOSTS)
    if not trusted:
        logging.warning(
            f"UNTRUSTED UPLOAD HOST '{uh}' — not a known NIOME storage host "
            f"({', '.join(TRUSTED_UPLOAD_HOSTS)}). This task may be from an impostor trying to "
            f"harvest your submission. Set NIOME_STRICT_UPLOAD_HOST=1 to refuse these.")
        if os.environ.get("NIOME_STRICT_UPLOAD_HOST") == "1":
            logging.error(f"REFUSING to upload to untrusted host '{uh}' (strict mode)")
            return False
    return True


def build_and_upload(contract_url: str, hbb_ref_url: str, presigned_url: str) -> bool:
    t0 = time.time()
    if not _check_origin(contract_url, hbb_ref_url, presigned_url):
        return False
    contract = _download_json(contract_url)
    reference = _download_json(hbb_ref_url)
    _capture(contract, reference)
    missing = _validate(contract, reference)
    if missing:
        logger.error(f"LIVE SCHEMA MISMATCH vs reconstruction, missing {missing}. "
                     f"Contract keys={list(contract)} rules={list(contract.get('rules', {}))} "
                     f"reference keys={list(reference)}. Not uploading; captured for review.")
        return False
    sub = solve(contract, reference, CHR11_FA)
    body = json.dumps(sub)
    logger.info(f"built {len(sub)} rows in {time.time()-t0:.2f}s, uploading")
    last = None
    for attempt in range(1, MAX_TRIES + 1):
        try:
            resp = httpx.put(presigned_url, content=body,
                             headers={"Content-Type": "application/json"}, timeout=120)
            resp.raise_for_status()
            logger.info(f"uploaded submission ({len(sub)} rows), total {time.time()-t0:.2f}s")
            return True
        except Exception as e:
            last = e; logger.warning(f"upload attempt {attempt} failed: {e}"); time.sleep(2 ** attempt)
    logger.error(f"upload failed after {MAX_TRIES} tries: {last}")
    return False


async def process_task(self, task, presigned_url: str) -> None:
    """Async drop-in. Runs the CPU build off the event loop so /forward stays instant."""
    import asyncio
    try:
        await asyncio.to_thread(build_and_upload, task.contract_url, task.hbb_ref_url, presigned_url)
    except Exception as e:
        logger.error(f"process_task error: {e}")
