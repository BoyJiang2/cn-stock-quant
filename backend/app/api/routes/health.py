from fastapi import APIRouter

router = APIRouter()


@router.get("/")
def root() -> dict[str, str]:
    return {"name": "CN Stock Quant API", "status": "ok"}


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

