.PHONY: help build test test-go test-py test-cli fmt lint clean dev install golden

GOBIN := isoforged
PLATFORMS := darwin/arm64 darwin/amd64 linux/arm64 linux/amd64

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

build: ## Build the Go daemon for this platform
	go build -trimpath -ldflags="-s -w" -o $(GOBIN) ./cmd/isoforged

build-all: ## Cross-compile the daemon for every supported platform
	@mkdir -p dist
	@for platform in $(PLATFORMS); do \
		os=$${platform%/*}; arch=$${platform#*/}; \
		echo "  building $$os/$$arch"; \
		GOOS=$$os GOARCH=$$arch CGO_ENABLED=0 \
			go build -trimpath -ldflags="-s -w" \
			-o dist/isoforged-$$os-$$arch ./cmd/isoforged || exit 1; \
	done
	@ls -lh dist/

test: test-go test-py test-cli ## Run every test suite

test-go: ## Run Go tests
	go test ./...

test-py: ## Run the Python service tests
	cd services && uv run --extra dev --extra raster pytest -q

test-cli: ## Run the CLI tests
	cd cli && uv run --extra dev pytest -q

golden: ## Regenerate render golden files (review the diff before committing)
	cd services && ISOFORGE_UPDATE_GOLDEN=1 uv run --extra dev --extra raster pytest -q
	cd services && uv run python -c "import json,glob,sys; sys.path.insert(0,'.');\
from isoforge_py.isodsl.canonical import scene_hash;\
print('\n'.join(scene_hash(json.load(open(f)))+' '+f.split('/')[-1] \
for f in sorted(glob.glob('../testdata/scenes/valid/*.json'))))" > testdata/golden/scene-hashes.txt
	@echo "regenerated; verify with: git diff testdata/golden"

fmt: ## Format Go and Python sources
	gofmt -w cmd internal web
	cd services && uv run --extra dev ruff format isoforge_py tests
	cd cli && uv run --extra dev ruff format isoforge tests

lint: ## Vet and lint
	go vet ./...
	@test -z "$$(gofmt -l cmd internal web)" || (echo "gofmt needed:"; gofmt -l cmd internal web; exit 1)
	cd services && uv run --extra dev ruff check isoforge_py
	cd cli && uv run --extra dev ruff check isoforge

install: build ## Install the CLI with the daemon bundled
	@mkdir -p cli/isoforge/bin
	cp $(GOBIN) cli/isoforge/bin/
	cd cli && uv pip install -e .
	@echo "installed; try: isoforge doctor"

dev: build ## Start the stack for manual testing
	@echo "starting services; Ctrl-C to stop"
	cd cli && uv run isoforge chat

clean: ## Remove build artifacts
	rm -rf $(GOBIN) dist cli/isoforge/bin
	find . -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	find . -name .pytest_cache -prune -exec rm -rf {} + 2>/dev/null || true
