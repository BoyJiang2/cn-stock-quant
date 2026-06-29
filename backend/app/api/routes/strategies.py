from fastapi import APIRouter

from app.strategy.registry import list_strategies

router = APIRouter()


@router.get("")
def strategies() -> list[dict]:
    return list_strategies()
