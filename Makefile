.PHONY: help init test up down daily weekly api logs psql sample score

help:
	@grep -E '^[a-z-]+:.*?##' Makefile | sed 's/:.*##/ —/'

init: ## create DB tables + dirs
	depotmon init

test: ## run the unit tests
	pytest -q

up: ## start db + api
	docker compose up -d db api

down: ## stop everything
	docker compose down

api: ## run the API locally (uvicorn --reload)
	uvicorn depotmon.api.app:app --host 0.0.0.0 --port 8080 --reload

daily: ## full daily cycle inside the worker container
	docker compose run --rm worker depotmon daily

weekly: ## weekly report inside the worker container
	docker compose run --rm worker depotmon weekly

logs: ## tail the api logs
	docker compose logs -f api

psql: ## open a psql shell on the database
	docker compose exec db psql -U depotmon -d depotmon

sample: ## draw the 30-site validation sample
	python validation/validate.py sample --n 30

score: ## score the filled-in validation sample
	python validation/validate.py score
