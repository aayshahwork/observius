.PHONY: dev test lint typecheck build migrate load-test

dev:
	docker compose up --build

test:
	cd api && pytest -x -v
	cd dashboard && npm test

lint:
	ruff check api/ workers/ sdk/ shared/
	cd dashboard && npm run lint

typecheck:
	mypy api/ workers/
	cd dashboard && npx tsc --noEmit

build:
	docker compose build

migrate:
	cd api && alembic upgrade head

load-test:
	pip install -r tests/load/requirements.txt && locust -f tests/load/locustfile.py --config tests/load/locust.conf
