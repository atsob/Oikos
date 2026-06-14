# Personal Finance – FastAPI Backend

REST API layer for the iOS React Native app. Sits in front of the existing PostgreSQL database.

## Setup

```bash
cd api
pip install -r requirements.txt

# Run (from the project root so imports work)
cd ..
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

API docs available at http://localhost:8000/docs

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/health | Health check |
| GET | /api/dashboard/accounts | All accounts with balance |
| GET | /api/dashboard/net-worth | Historical net worth by month |
| GET | /api/dashboard/monthly-summaries | AI monthly summaries |
| GET | /api/register/transactions | Paginated transaction list per account |
| GET | /api/reports/income-expense | Monthly income vs expense |
| GET | /api/reports/top-categories | Top N income/expense categories |
| GET | /api/reports/savings-rate | Monthly savings rate |
| GET | /api/reports/portfolio-summary | Current holdings with EUR value |
