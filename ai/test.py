import psycopg2
from ollama import Client

# Connect with Ollama on Pi
client = Client(host='http://192.168.4.20:11434')

def ask_my_finances(question):
    # 1. Change the question to an Embedding
    q_res = client.embeddings(model="nomic-embed-text", prompt=question)
    q_vector = q_res["embedding"]

    # 2. Connect to the database and search (Similarity Search)
    # We search in the transactions for the 5 most relevant transactions
    conn = psycopg2.connect(host="192.168.4.20", dbname="Finance", user="admin", password="31.12.1969")
    cur = conn.cursor()
    
    search_query = """
    SELECT description, total_amount, date 
    FROM Transactions 
    ORDER BY embedding <=> %s::vector 
    LIMIT 5;
    """
    
    cur.execute(search_query, (q_vector,))
    rows = cur.fetchall()
    
    # 3. Context creation for the Llama
    context = "\n".join([f"Amount: {r[1]}, Description: {r[0]}, Date: {r[2]}" for r in rows])
    
    # 4. Final question to the Llama 3.2 3B
    prompt = f"""
    You are a financial advisor. Use the following transactions from my database to answer the question.
    If you don't know the answer, just say so.

    Transactions:
    {context}

    Question: {question}
    Answer:"""

    response = client.generate(model="llama3.2:3b", prompt=prompt)
    print("\n--- AI Response ---")
    print(response['response'])

    cur.close()
    conn.close()

# Testing
if __name__ == "__main__":
    query = "What were my largest expense transactions during the last 12 months and what did they relate to?"
    ask_my_finances(query)
