import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from huggingface_hub import HfApi, whoami
from huggingface_hub.errors import HfHubHTTPError, LocalTokenNotFoundError

logger = logging.getLogger(__name__)

# Two layouts in the wild: v3 packs episodes into shared chunk files, v2 has one parquet per episode.
_EPISODE_ZERO_PATHS = (
    "data/chunk-000/file-000.parquet",
    "data/chunk-000/episode_000000.parquet",
)


def _has_episode_zero(api: HfApi, repo_id: str) -> bool:
    for path in _EPISODE_ZERO_PATHS:
        try:
            if api.file_exists(repo_id, path, repo_type="dataset"):
                return True
        except HfHubHTTPError:
            continue
    return False


def list_user_datasets() -> list[dict[str, Any]]:
    try:
        info = whoami()
    except (LocalTokenNotFoundError, HfHubHTTPError, OSError):
        return []

    authors = [info["name"]] + [o["name"] for o in info.get("orgs", [])]
    api = HfApi()
    seen: set[str] = set()
    candidates: list[dict[str, Any]] = []
    for author in authors:
        try:
            for ds in api.list_datasets(author=author, filter="LeRobot", limit=200):
                if ds.id in seen:
                    continue
                seen.add(ds.id)
                candidates.append({
                    "repo_id": ds.id,
                    "last_modified": ds.last_modified.isoformat() if ds.last_modified else None,
                    "private": bool(getattr(ds, "private", False)),
                })
        except HfHubHTTPError as e:
            logger.warning(f"list_datasets({author}) failed: {e}")

    if not candidates:
        return []

    with ThreadPoolExecutor(max_workers=10) as pool:
        flags = list(pool.map(lambda d: _has_episode_zero(api, d["repo_id"]), candidates))
    out = [d for d, ok in zip(candidates, flags) if ok]

    out.sort(key=lambda d: d["last_modified"] or "", reverse=True)
    return out
