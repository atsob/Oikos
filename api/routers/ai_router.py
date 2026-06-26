"""AI assistant endpoint."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


class AskRequest(BaseModel):
    question: str


@router.get("/embedding-status")
def embedding_status():
    from database.connection import get_db
    import pandas as pd
    with get_db() as conn:
        df = pd.read_sql(
            """SELECT COUNT(*) AS total,
                      SUM(CASE WHEN embedding IS NOT NULL THEN 1 ELSE 0 END) AS indexed
               FROM Transactions
               WHERE total_amount <> 0
                 AND accounts_id_target IS NULL""",
            conn,
        )
    row = df.iloc[0]
    return {"total": int(row["total"]), "indexed": int(row["indexed"])}


@router.post("/update-embeddings")
def update_embeddings_endpoint():
    try:
        from ai.update_vector import update_all_embeddings
        update_all_embeddings()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(503, f"Embedding update failed: {e}")


@router.post("/ask")
async def ask_ai(req: AskRequest):
    import asyncio
    from fastapi import HTTPException

    def _run():
        from ai.llm import init_llm
        from ai.agent import create_ai_agent
        from ai.rag import PgVectorRagEngine
        from database.connection import get_sql_database

        llm = init_llm()
        db = get_sql_database()
        rag = PgVectorRagEngine()
        agent = create_ai_agent(llm, db, rag)
        result = agent.invoke({"input": req.question})

        steps = []
        for action, observation in result.get("intermediate_steps", []):
            steps.append({
                "thought": getattr(action, "log", "").strip(),
                "tool": getattr(action, "tool", ""),
                "tool_input": str(getattr(action, "tool_input", "")),
                "observation": str(observation)[:2000],
            })

        raw = result.get("output") or result.get("answer") or str(result)
        # Extract only the Final Answer if the model leaked its reasoning into output
        import re
        m = re.search(r'final answer[:\s]+(.*)', raw, re.IGNORECASE | re.DOTALL)
        answer = m.group(1).strip() if m else re.sub(r'^(Thought:.*?\n)+', '', raw, flags=re.DOTALL).strip()
        return {"answer": answer, "steps": steps}

    try:
        data = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(None, _run),
            timeout=300,
        )
        return data
    except asyncio.TimeoutError:
        raise HTTPException(504, "AI request timed out after 5 minutes")
    except Exception as e:
        raise HTTPException(503, f"AI unavailable: {e}")
