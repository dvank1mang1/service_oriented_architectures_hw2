.PHONY: setup up down logs generate test lint demo db

setup:
	python3 scripts/setup.py
up:
	docker compose up --build -d --wait
down:
	docker compose down
logs:
	docker compose logs -f api
generate:
	docker build --target codegen -t marketplace-hw2-codegen .
	mkdir -p generated
	docker run --rm --entrypoint sh -v "$(CURDIR)/generated:/export" marketplace-hw2-codegen -c 'cp -R /build/generated/. /export/'
test:
	sh scripts/test.sh
lint:
	ruff check app scripts tests
demo:
	docker compose exec api python scripts/demo.py
db:
	docker compose exec db psql -U marketplace -d marketplace
