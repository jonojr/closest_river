
.PHONY: dev-build
dev-build:
	docker compose -f docker-compose.local.yml build
.PHONY: dev-up
dev-up:
	docker compose -f docker-compose.local.yml -f docker-compose.docs.yml up
.PHONY: dev-prompt
dev-prompt:
	docker exec --tty --interactive closest_river_local_django /bin/bash
