import psycopg2
import ollama
from database.connection import get_connection
from ollama import Client
from sqlalchemy import Column, Float, text
from pgvector.sqlalchemy import Vector 
from config.settings import ENV_CONFIG, OLLAMA_URL

client = Client(host=OLLAMA_URL)

def update_embeddings(table_name, text_template, id_col, conn, cur):
    CHUNK_SIZE = 25  # Small size for devices with low memory (Pi/Mobile)
    # text_template: a function that creates the string
    try:
        if table_name == "Transactions":
            query = """
                SELECT  DISTINCT t.transactions_id, 
                        t.date, 
                        t.description, 
                        CAST(t.total_amount AS DECIMAL(15,2)) as total_amount,
                        t.accounts_id,
                        a.accounts_name,
                        a.accounts_type,
                        curr.currencies_shortname,
                        (SELECT COALESCE(Payees_Name, 'UNKNOWN') FROM Payees WHERE Payees_Id = t.payees_id) as payee_name,
						COALESCE(
						    (WITH RECURSIVE CategoryHierarchy AS (
						        SELECT Categories_Id, Categories_Name::TEXT as Full_Path
						        FROM Categories 
						        WHERE Categories_Id_Parent IS NULL
						        UNION ALL
						        SELECT c1.Categories_Id, ch.Full_Path || ' : ' || c1.Categories_Name
						        FROM Categories c1
						        JOIN CategoryHierarchy ch ON c1.Categories_Id_Parent = ch.Categories_Id
						    )
						    SELECT Full_Path
						    FROM CategoryHierarchy
						    WHERE Categories_Id = s.Categories_Id 
						    LIMIT 1 
						    ), 
						    'Money Transfer'
						) as category_path
                FROM    Transactions t,
                        Splits s,
                        Accounts a,
                        Currencies curr
                WHERE   t.embedding IS NULL 
                AND     s.transactions_id = t.transactions_id
                AND     a.accounts_id = t.accounts_id
                AND     curr.currencies_id = a.currencies_id
				AND		t.total_amount <> 0
				AND		s.Amount <> 0
				AND		ABS(s.Amount) = (SELECT MAX(ABS(Amount)) FROM Splits WHERE transactions_id = t.transactions_id)
            """
        elif table_name == "Splits":
            query = """
                SELECT  DISTINCT s.splits_id,
						t.transactions_id, 
                        t.date, 
                        t.description, 
                        CAST(s.Amount AS DECIMAL(15,2)) as split_amount,
                        t.accounts_id,
                        a.accounts_name,
                        a.accounts_type,
                        curr.currencies_shortname,
                        (SELECT COALESCE(Payees_Name, 'UNKNOWN') FROM Payees WHERE Payees_Id = t.payees_id) as payee_name,
						COALESCE(
						    (WITH RECURSIVE CategoryHierarchy AS (
						        SELECT Categories_Id, Categories_Name::TEXT as Full_Path
						        FROM Categories 
						        WHERE Categories_Id_Parent IS NULL
						        UNION ALL
						        SELECT c1.Categories_Id, ch.Full_Path || ' : ' || c1.Categories_Name
						        FROM Categories c1
						        JOIN CategoryHierarchy ch ON c1.Categories_Id_Parent = ch.Categories_Id
						    )
						    SELECT Full_Path
						    FROM CategoryHierarchy
						    WHERE Categories_Id = s.Categories_Id 
						    LIMIT 1 
						    ), 
						    'Money Transfer'
						) as category_path
                FROM    Transactions t,
                        Splits s,
                        Accounts a,
                        Currencies curr
                WHERE   s.embedding IS NULL 
                AND     s.transactions_id = t.transactions_id
                AND     a.accounts_id = t.accounts_id
                AND     curr.currencies_id = a.currencies_id
				AND		t.total_amount <> 0
				AND		s.Amount <> 0
            """
        elif table_name == "Accounts":
            query = """
                SELECT 
                    A.accounts_id, 
                    A.accounts_name,
                    A.accounts_type,
                    COALESCE ((SELECT F.Institutions_Name FROM Institutions F WHERE F.Institutions_Id = A.Institutions_Id), 'UKNOWN') as institution_name,
                    CAST(A.Accounts_Balance AS DECIMAL(15,2)) as accounts_balance,
                    COALESCE ((SELECT Cur.Currencies_ShortName FROM Currencies Cur WHERE Cur.Currencies_Id = A.Currencies_Id), 'UKNOWN') as currency,
                    A.is_active
                FROM Accounts A
            """
        elif table_name == "Investments":
            query = """
                SELECT	t.investments_id, 
                        a.accounts_name,
                        t.action, 
                        COALESCE(s.Securities_Name, 'NO SECURITY') AS security,
                        t.date, 
                        CAST(t.quantity AS DECIMAL(25,8)) as quantity,
                        CAST(t.price_per_share AS DECIMAL(15,2)) as price,
                        CAST(t.commission AS DECIMAL(15,2)) as commission,
                        CAST(t.total_amount_acccur AS DECIMAL(15,2)) as total_amount,
                        c.Currencies_ShortName
                FROM	Investments t
                JOIN    Accounts a ON t.accounts_id = a.accounts_id
                LEFT JOIN Securities s ON s.Securities_Id = t.Securities_Id
                LEFT JOIN Currencies c ON c.Currencies_Id = a.Currencies_Id
                WHERE	t.embedding IS NULL
            """ 
        elif table_name == "Holdings":
            query = """
                WITH CalculatedHoldings AS (
                    SELECT	h.holdings_id, 
                            a.accounts_name,
                            COALESCE(s.Securities_Name, 'NO SECURITY') AS security,
                            CAST(h.quantity AS DECIMAL(25,8)) as quantity,
                            COALESCE(CAST(h.fifo_avg_price AS DECIMAL(15,2)), 0) as fifo_avg_price,
                            COALESCE((
                                SELECT CAST(hp.Close AS DECIMAL(15,2)) 
                                FROM Historical_Prices hp 
                                WHERE hp.Securities_Id = s.Securities_Id 
                                ORDER BY hp.Date DESC 
                                LIMIT 1
                            ), 0) as last_price,
                            c.Currencies_ShortName
                    FROM	Holdings h
                    JOIN    Accounts a ON h.accounts_id = a.accounts_id
                    LEFT JOIN Securities s ON s.Securities_Id = h.Securities_Id
                    LEFT JOIN Currencies c ON c.Currencies_Id = a.Currencies_Id
                )
                SELECT 
                    *,
                    CAST((quantity * last_price) AS DECIMAL(15,2)) AS current_value,
                    CAST((quantity * last_price) - (quantity * fifo_avg_price) AS DECIMAL(15,2)) AS unrealized_gain -- Επιπλέον χρήσιμο πεδίο
                FROM CalculatedHoldings
                ORDER BY accounts_name ASC, security ASC
            """

        cur.execute(query)
        columns = [desc[0] for desc in cur.description]
        rows = cur.fetchall()

        if not rows:
            return

        data_list = [dict(zip(columns, row)) for row in rows]
        total_rows = len(data_list)
        
        print(f"Processing {total_rows} rows for {table_name} in chunks of {CHUNK_SIZE}...")

        # Divide the list in chunks of {CHUNK_SIZE}
        for i in range(0, total_rows, CHUNK_SIZE):
            chunk = data_list[i : i + CHUNK_SIZE]
            chunk_texts = [text_template(item) for item in chunk]
            
            # Κλήση στο Ollama για το τρέχον πακέτο
            response = client.embed(
                model="nomic-embed-text", 
                input=chunk_texts
            )
            
            embeddings_list = response["embeddings"]

            # Update in the database for each item in the current chunk
            for idx, item in enumerate(chunk):
                cur.execute(
                    f"UPDATE {table_name} SET embedding = %s WHERE {id_col} = %s", 
                    (embeddings_list[idx], item[id_col])
                )
            
            # Commit per chunk to save the results incrementally
            conn.commit()
            print(f"   Completed {min(i + CHUNK_SIZE, total_rows)}/{total_rows} rows...")

    except Exception as e:
        print(f"Warning: Could not update embeddings for {table_name}: {e}")
        conn.rollback()


# --- Example templates for your database ---

def bank_tx_template(row):
  #  return f"Transaction on {row['date']}: {row['description']}. Total amount: {row['total_amount']}. Account ID: {row['accounts_id']}."
    return f"Transaction on {row['date']}: with Payee: {row['payee_name']} and Category: {row['category_path']} and Description: {row['description']}. Total Amount: {row['total_amount']} {row['currencies_shortname']}. Account Name: {row['accounts_name']} and Account Type: {row['accounts_type']}."

def bank_tx_split_template(row):
  #  return f"Transaction on {row['date']}: {row['description']}. Total amount: {row['total_amount']}. Account ID: {row['accounts_id']}."
    return f"Transaction Split on {row['date']}: with Payee: {row['payee_name']} and Category: {row['category_path']} and Description: {row['description']}. Split Amount: {row['split_amount']} {row['currencies_shortname']}. Account Name: {row['accounts_name']} and Account Type: {row['accounts_type']}."

def account_template(row):
    return f"Institution: {row['institution_name']} - Account {row['accounts_name']} of type {row['accounts_type']} with balance {row['accounts_balance']} {row['currency']}. Is Active: {row['is_active']}."

def investment_template(row):
    return f"Investment transaction on Account {row['accounts_name']}, Action: {row['action']} for Security {row['security']} on {row['date']}. Quantity: {row['quantity']}, Price: {row['price']}, Commission: {row['commission']} - Total Amount: {row['total_amount']} {row['currencies_shortname']}."

def holdings_template(row):
    return f"Holding on Account: {row['accounts_name']} for Security: {row['security']} - Quantity: {row['quantity']}, FIFO Average Price: {row['fifo_avg_price']}, Last Price: {row['last_price']} - Current Value: {row['current_value']} - Unrealized Gain: {row['unrealized_gain']} {row['currencies_shortname']}."

def update_all_embeddings():
    try:
        conn = get_connection()
        cur = conn.cursor()
        update_embeddings("Transactions", bank_tx_template, "transactions_id", conn, cur)
        update_embeddings("Splits", bank_tx_split_template, "splits_id", conn, cur)
        update_embeddings("Accounts", account_template, "accounts_id", conn, cur)
        update_embeddings("Investments", investment_template, "investments_id", conn, cur)
        update_embeddings("Holdings", holdings_template, "holdings_id", conn, cur)
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Warning: Embedding update failed: {e}")

# Execution
if __name__ == "__main__":
    update_all_embeddings()

