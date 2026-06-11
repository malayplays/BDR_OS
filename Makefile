.PHONY: dev test e2e demo fixtures lint

dev:
	cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

test:
	cd backend && python -m pytest tests/ -v

e2e:
	cd backend && python -m pytest tests/e2e/ -v

demo:
	cd backend && python -m tests.e2e.demo_happy_week

fixtures:
	cd backend && python -m fixtures.generate

lint:
	cd backend && ruff check .
