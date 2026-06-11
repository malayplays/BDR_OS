.PHONY: dev test fixtures lint

dev:
	cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

test:
	cd backend && python -m pytest tests/ -v

fixtures:
	cd backend && python -m fixtures.generate

lint:
	cd backend && ruff check .
