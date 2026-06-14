# tg-viewer — Telegram data extraction toolkit
#
# Thin wrapper around ./tg-viewer plus dev shortcuts (tests, typecheck, codegen).
# Override variables on the command line, e.g.:
#   make decrypt DATA=./tg_2026-05-22_00-28-56
#   make webui  DATA=./tg_2026-05-22_00-28-56/parsed_data ACCOUNT=tehacepol
#   make full   PORT=5050

SHELL := /bin/bash

DATA     ?=
ACCOUNT  ?=
PORT     ?=
HOST     ?=
OLD      ?=
NEW      ?=
DEST     ?=
INTERVAL ?=
VAULT    ?=
SRC      ?=

# Import target: Telegram macOS group container holding the live decryption key,
# and the staging dir the copied account-* gets symlinked into.
TG_CONTAINER ?= $(HOME)/Library/Group Containers/6N38VWS5BX.ru.keepcoder.Telegram/appstore
IMPORT_DEST  ?= tg_imported
# Positional path: `make import /path/to/account-<id>` (falls back to SRC=/path).
IMPORT_SRC   := $(or $(strip $(filter-out import,$(MAKECMDGOALS))),$(SRC))

TG      := ./tg-viewer
TG_OPTS := $(if $(ACCOUNT),--account '$(ACCOUNT)') $(if $(PORT),--port '$(PORT)') $(if $(HOST),--host '$(HOST)')

.DEFAULT_GOAL := help

.PHONY: help setup backup decrypt parse webui dev full import clean \
        ghosts daemon watcher install-launchd uninstall-launchd launchd-status \
        test typecheck codegen web-install web-build web-dev

help:  ## Show this help
	@awk 'BEGIN {FS = ":.*?## "; printf "Targets:\n"} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo ""
	@echo "Variables:"
	@echo "  DATA, ACCOUNT, PORT, HOST    pipeline wrappers (e.g. make webui DATA=./tg_*/parsed_data)"
	@echo "  OLD, NEW                     ghosts diff snapshots (optional — auto-picks 2 newest)"
	@echo "  DEST, INTERVAL               daemon dest dir + poll seconds (default ./tg_continuous, 300)"
	@echo "  VAULT                        watcher content-addressed vault dir (default ./tg_vault)"
	@echo "  SRC, IMPORT_DEST             import a copied account-* dir (make import /path; staging ./tg_imported)"
	@echo "                               for paths with spaces or a leading '-', use the SRC= form:"
	@echo "                                 make import SRC='/path/with spaces/account-123'"

# ── tg-viewer wrappers ────────────────────────────────────────────────

setup:  ## Install Python + frontend dependencies
	$(TG) setup

backup:  ## Create Telegram backup (DATA=./dest optional)
	$(TG) backup '$(DATA)'

decrypt:  ## Decrypt databases (DATA=./tg_.../ required)
	@[ -n "$(DATA)" ] || { echo "DATA=./tg_.../ required" >&2; exit 2; }
	$(TG) decrypt '$(DATA)' $(TG_OPTS)

parse:  ## Parse Postbox binary (DATA=./tg_.../ required)
	@[ -n "$(DATA)" ] || { echo "DATA=./tg_.../ required" >&2; exit 2; }
	$(TG) parse '$(DATA)' $(TG_OPTS)

webui:  ## Start web UI (DATA=./tg_.../parsed_data required)
	@[ -n "$(DATA)" ] || { echo "DATA=./tg_.../parsed_data required" >&2; exit 2; }
	$(TG) webui '$(DATA)' $(TG_OPTS)

dev:  ## FastAPI + Bun HMR dev stack (DATA=./tg_.../parsed_data required)
	@[ -n "$(DATA)" ] || { echo "DATA=./tg_.../parsed_data required" >&2; exit 2; }
	$(TG) dev '$(DATA)' $(TG_OPTS)

full:  ## Full pipeline: backup → decrypt → parse → webui
	$(TG) full '$(DATA)' $(TG_OPTS)

import:  ## Import an already-copied account-* dir + reuse live key, then serve (make import /path/to/account-<id>; use SRC='…' for paths with spaces or leading '-')
	@src="$(IMPORT_SRC)"; \
	if [ -z "$$src" ]; then echo "usage: make import /path/to/account-<id>   (or SRC=/path)" >&2; \
	  echo "       for paths with spaces or a leading '-', use the SRC= form:" >&2; \
	  echo "         make import SRC='/path/with spaces/account-123'" >&2; exit 2; fi; \
	if [ ! -d "$$src" ]; then echo "not a directory: $$src" >&2; exit 2; fi; \
	key="$(TG_CONTAINER)/.tempkeyEncrypted"; \
	if [ ! -f "$$key" ]; then echo "live key not found: $$key" >&2; echo "(is Telegram installed for this user?)" >&2; exit 2; fi; \
	mkdir -p "$(IMPORT_DEST)"; \
	cp "$$key" "$(IMPORT_DEST)/.tempkeyEncrypted"; \
	cp "$(TG_CONTAINER)/accounts-shared-data" "$(IMPORT_DEST)/accounts-shared-data" 2>/dev/null || true; \
	case "$$(basename "$$src")" in \
	  account-*) ln -sfn "$$src" "$(IMPORT_DEST)/$$(basename "$$src")" ;; \
	  *) found=0; for a in "$$src"/account-*; do [ -d "$$a" ] || continue; ln -sfn "$$a" "$(IMPORT_DEST)/$$(basename "$$a")"; found=1; done; \
	     if [ "$$found" = 0 ]; then echo "no account-* dir at or under: $$src" >&2; exit 2; fi ;; \
	esac; \
	echo "Staged into $(IMPORT_DEST)/ — running decrypt → parse → webui"; \
	$(TG) decrypt "$(IMPORT_DEST)" $(TG_OPTS) && \
	$(TG) parse   "$(IMPORT_DEST)" $(TG_OPTS) && \
	$(TG) webui   "$(IMPORT_DEST)/parsed_data" $(TG_OPTS)

clean:  ## Remove all backup, decrypted, and parsed data
	$(TG) clean

# ── 24/7 capture + launchd ────────────────────────────────────────────

ghosts:  ## Diff two parsed_data snapshots (OLD=… NEW=…; auto-pick newest two if unset)
	$(TG) ghosts $(OLD) $(NEW)

daemon:  ## Periodic backup→decrypt→parse loop (DEST=./tg_continuous INTERVAL=300)
	$(TG) daemon $(DEST) $(INTERVAL)

watcher:  ## FSEvents watcher → content-addressed vault (VAULT=./tg_vault)
	$(TG) watcher $(VAULT)

install-launchd:  ## Install daemon + watcher as launchd agents (auto-start at login)
	$(TG) install-launchd

uninstall-launchd:  ## Remove the launchd agents
	$(TG) uninstall-launchd

launchd-status:  ## Show state of both launchd agents
	$(TG) launchd-status

# ── Development ───────────────────────────────────────────────────────

test:  ## Run backend pytest suite
	pytest

typecheck:  ## Frontend TypeScript check
	cd apps/web && bun run typecheck

codegen:  ## Regenerate OpenAPI types from running API
	cd apps/web && bun run codegen

web-install:  ## Install frontend dependencies (apps/web)
	cd apps/web && bun install

web-build:  ## Build frontend production bundle → apps/web/dist
	cd apps/web && bun run build

web-dev:  ## Frontend dev server only (no API; use `make dev` for full stack)
	cd apps/web && bun run dev

# `make import /path/to/account-<id>` parses the path as a phantom goal. Swallow
# it as a no-op — but ONLY while `import` runs, so typos in other targets still
# error normally.
#
# Limitations of the positional form (it reads $(MAKECMDGOALS)):
#   - a misspelled sibling target alongside `import` is silently swallowed here
#     instead of erroring;
#   - paths with spaces are split into multiple goals, and a path with a leading
#     '-' is mis-read as a flag.
# For any path with spaces or a leading '-', use the explicit SRC= form instead:
#     make import SRC='/path/with spaces/account-123'
ifneq (,$(filter import,$(MAKECMDGOALS)))
%:
	@:
endif
