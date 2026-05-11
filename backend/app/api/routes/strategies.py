from fastapi import APIRouter

from app.strategy.registry import list_strategies

router = APIRouter()


@router.get("")
def strategies() -> list[dict[str, str]]:
    return list_strategies()

