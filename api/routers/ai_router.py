"""AI assistant endpoint."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


class AskRequest(BaseModel):
    question: str


@router.post("/ask")
def ask_ai(req: AskRequest):
    try:
        from ai.agent import run_agent
        answer = run_agent(req.question)
        return {"answer": answer}
    except ImportError:
        raise HTTPException(503, "AI module not available")
    except Exception as e:
        raise HTTPException(500, str(e))
