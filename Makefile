.PHONY: dev test lint typecheck build migrate reset-db fresh load-test

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
	docker compose run --rm migrate

reset-db:
	docker compose exec postgres dropdb -U postgres --if-exists computeruse
	docker compose exec postgres createdb -U postgres computeruse
	docker compose run --rm migrate

fresh:
	docker compose down -v
	$(MAKE) dev

load-test:
	pip install -r tests/load/requirements.txt && locust -f tests/load/locustfile.py --config tests/load/locust.conf

setup:
	./scripts/setup.sh

logs:
	docker compose logs -f

logs-api:
	docker compose logs -f api

logs-worker:
	docker compose logs -f worker

shell-db:
	docker compose exec postgres psql -U postgres -d computeruse

shell-api:
	docker compose exec api bash
