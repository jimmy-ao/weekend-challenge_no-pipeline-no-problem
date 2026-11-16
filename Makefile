.PHONY: help build test plan deploy dev destroy

TF := terraform -chdir=infra

help: ## Show targets
	@grep -E '^[a-z]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-8s %s\n", $$1, $$2}'

build: ## Build the Lambda packages into build/
	./scripts/build.sh

test: ## Run the backend tests against moto (no AWS account needed)
	uv run pytest -q

plan: build ## terraform plan
	$(TF) init -backend-config=backend.hcl -upgrade=false -input=false >/dev/null
	$(TF) plan

deploy: build ## Build and deploy everything (frontend included) with terraform apply
	$(TF) init -backend-config=backend.hcl -upgrade=false -input=false >/dev/null
	$(TF) apply
	@printf "\nOpen: %s\n" "$$($(TF) output -raw site_url)"

dev: ## Serve the frontend on http://localhost:5173 against the deployed backend
	$(TF) output -raw frontend_config_js > frontend/config.js
	uv run --no-sync python -m http.server 5173 --directory frontend

destroy: ## Delete everything
	$(TF) destroy
