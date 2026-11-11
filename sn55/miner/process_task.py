"""Drop-in body for neurons/miner.py::Miner.process_task (genomesio/subnet-niome @0c03fef).

The validator sends a GenomicsTaskSynapse carrying task.contract_url, task.hbb_ref_url and a
presigned PUT url. We download the two inputs, build a submission on CPU (<1s), and PUT it well
inside the 300s window. Fully self-contained, fault-tolerant, one submission per task.

Install: copy this logic into Miner.process_task, ensure CHR11_FA points at the Ensembl r116
chr11 (see deploy/README). Nothing here touches wallets or chain state."""
import json, logging, os, time, urllib.request
import httpx
from miner.solve import solve

logger = logging.getLogger(__name__)
CHR11_FA = os.environ.get("CHR11_FA", os.path.expanduser("~/niome-data/chr11.fa"))
HTTP_TIMEOUT = 60
MAX_TRIES = 3


def _download_json(url: str) -> dict:
    last = None
    for attempt in range(1, MAX_TRIES + 1):
        try:
            with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as r:
                return json.loads(r.read())
        except Exception as e:
            last = e; time.sleep(2 ** attempt)
    raise last


def build_and_upload(contract_url: str, hbb_ref_url: str, presigned_url: str) -> bool:
    t0 = time.time()
    contract = _download_json(contract_url)
    reference = _download_json(hbb_ref_url)
    sub = solve(contract, reference, CHR11_FA)
    body = json.dumps(sub)
    logger.info(f"built {len(sub)} rows in {time.time()-t0:.2f}s, uploading")
    last = None
    for attempt in range(1, MAX_TRIES + 1):
        try:
            resp = httpx.put(presigned_url, content=body,
                             headers={"Content-Type": "application/json"}, timeout=120)
            resp.raise_for_status()
            logger.info(f"uploaded submission for task, total {time.time()-t0:.2f}s")
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
