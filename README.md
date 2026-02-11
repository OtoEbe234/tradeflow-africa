# TradeFlow Africa

Cross-border B2B payment platform for the Nigeria-China trade corridor.

TradeFlow Africa enables Nigerian and Chinese traders to settle cross-border payments efficiently using a peer-to-peer matching engine backed by Afrexim CIPS for settlement.

## Architecture

- **Backend:** Python 3.12 + FastAPI
- **Database:** PostgreSQL (Supabase)
- **Cache / Queue broker:** Redis (Upstash)
- **Task queue:** Celery
- **WhatsApp integration:** Meta Cloud API
- **Authentication:** JWT with RS256
- **Settlement:** Providus Bank (NGN) + Afrexim CIPS (CNY)

## Quick Start

### Prerequisites

- Python 3.12+
- Docker & Docker Compose (for local Postgres + Redis)

### Local Development

```bash
# Clone and enter the project
cd tradeflow-africa

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy environment variables
cp .env.example .env
# Edit .env with your actual values

# Start Postgres + Redis
docker compose up postgres redis -d

# Run database migrations
alembic upgrade head

# Seed test data (optional)
python scripts/seed_data.py

# Start the API server
uvicorn app.main:app --reload
```

### Full Stack with Docker

```bash
docker compose up --build
```

The API will be available at `http://localhost:8000`.

## API Documentation

Once running, interactive docs are available at:

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

## Key Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| POST | `/api/v1/auth/register` | Register a new trader |
| POST | `/api/v1/auth/login` | Login with phone + OTP |
| POST | `/api/v1/transactions` | Create a transaction |
| GET | `/api/v1/rates/ngn-cny` | Get current NGN/CNY rate |
| POST | `/api/v1/matching/trigger` | Trigger matching cycle (admin) |

## Project Structure

```
app/
  api/            # FastAPI route handlers
  models/         # SQLAlchemy ORM models
  schemas/        # Pydantic request/response schemas
  services/       # Business logic (KYC, payments, rates)
  matching_engine/# P2P matching engine
  whatsapp/       # WhatsApp bot and conversation flows
  tasks/          # Celery background tasks
tests/            # Test suite
alembic/          # Database migrations
scripts/          # Utility scripts
```

## Testing

```bash
pytest --cov=app tests/
```

## License

Proprietary. All rights reserved.
