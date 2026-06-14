from ollama import Client
from database.connection import get_connection
from config.settings import ENV_CONFIG


def _embed(text: str) -> list[float]:
    client = Client(host=ENV_CONFIG['OLLAMA_URL'])
    return client.embeddings(model="nomic-embed-text", prompt=text)["embedding"]


def semantic_search(question: str, top_k: int = 10) -> str:
    """Search transactions by meaning using pgvector similarity."""
    try:
        q_vector = _embed(question)
    except Exception as e:
        return f"Could not generate embedding: {e}"

    conn = get_connection()
    try:
        cur = conn.cursor()
        # Check that any embeddings exist at all before running the <=> operator
        cur.execute("SELECT 1 FROM Transactions WHERE embedding IS NOT NULL LIMIT 1")
        if not cur.fetchone():
            return (
                "No transaction embeddings found. "
                "Click 'Update Vendor Embeddings' on the AI Assistant page first."
            )

        cur.execute(
            """
            SELECT DISTINCT
                t.date,
                t.description,
                CAST(t.total_amount AS DECIMAL(15,2))  AS total_amount,
                curr.currencies_shortname               AS currency,
                a.accounts_name,
                COALESCE(
                    (SELECT Payees_Name FROM Payees WHERE Payees_Id = t.payees_id),
                    'UNKNOWN'
                )                                       AS payee,
                COALESCE(
                    (WITH RECURSIVE ch AS (
                        SELECT Categories_Id,
                               Categories_Name::TEXT AS Full_Path
                        FROM   Categories
                        WHERE  Categories_Id_Parent IS NULL
                        UNION ALL
                        SELECT c.Categories_Id,
                               ch.Full_Path || ' : ' || c.Categories_Name
                        FROM   Categories c
                        JOIN   ch ON c.Categories_Id_Parent = ch.Categories_Id
                    )
                    SELECT Full_Path FROM ch
                    WHERE  Categories_Id = s.Categories_Id
                    LIMIT  1),
                    'Money Transfer'
                )                                       AS category
            FROM  Transactions t
            JOIN  Splits      s    ON s.transactions_id = t.transactions_id
            JOIN  Accounts    a    ON a.accounts_id     = t.accounts_id
            JOIN  Currencies  curr ON curr.currencies_id = a.currencies_id
            WHERE t.embedding IS NOT NULL
              AND t.total_amount <> 0
              AND s.Amount       <> 0
              AND ABS(s.Amount)  = (
                      SELECT MAX(ABS(Amount)) FROM Splits
                      WHERE  transactions_id = t.transactions_id)
            ORDER BY t.embedding <=> %s::vector
            LIMIT %s
            """,
            (q_vector, top_k),
        )
        rows = cur.fetchall()
        colnames = [d[0] for d in cur.description]
        cur.close()
    except Exception as e:
        return f"Semantic search failed: {e}"
    finally:
        conn.close()

    if not rows:
        return "No relevant transactions found."

    header = "| " + " | ".join(colnames) + " |"
    sep    = "| " + " | ".join(["---"] * len(colnames)) + " |"
    body   = "\n".join(
        "| " + " | ".join("" if v is None else str(v) for v in row) + " |"
        for row in rows
    )
    return f"**Most relevant transactions:**\n\n{header}\n{sep}\n{body}"


class PgVectorRagEngine:
    """Drop-in replacement for a LlamaIndex query engine, backed by pgvector."""

    def query(self, question: str) -> str:
        return semantic_search(question)
