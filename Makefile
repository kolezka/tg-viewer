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

TG      := ./tg-viewer
TG_OPTS := $(if $(ACCOUNT),--account $(ACCOUNT)) $(if $(PORT),--port $(PORT)) $(if $(HOST),--host $(HOST))

.DEFAULT_GOAL := help

.PHONY: help setup backup decrypt parse webui dev full clean \
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

# ── tg-viewer wrappers ────────────────────────────────────────────────

setup:  ## Install Python + frontend dependencies
	$(TG) setup

backup:  ## Create Telegram backup (DATA=./dest optional)
	$(TG) backup $(DATA)

decrypt:  ## Decrypt databases (DATA=./tg_.../ required)
	@[ -n "$(DATA)" ] || { echo "DATA=./tg_.../ required" >&2; exit 2; }
	$(TG) decrypt $(DATA) $(TG_OPTS)

parse:  ## Parse Postbox binary (DATA=./tg_.../ required)
	@[ -n "$(DATA)" ] || { echo "DATA=./tg_.../ required" >&2; exit 2; }
	$(TG) parse $(DATA) $(TG_OPTS)

webui:  ## Start web UI (DATA=./tg_.../parsed_data required)
	@[ -n "$(DATA)" ] || { echo "DATA=./tg_.../parsed_data required" >&2; exit 2; }
	$(TG) webui $(DATA) $(TG_OPTS)

dev:  ## FastAPI + Bun HMR dev stack (DATA=./tg_.../parsed_data required)
	@[ -n "$(DATA)" ] || { echo "DATA=./tg_.../parsed_data required" >&2; exit 2; }
	$(TG) dev $(DATA) $(TG_OPTS)

full:  ## Full pipeline: backup → decrypt → parse → webui
	$(TG) full $(DATA) $(TG_OPTS)

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
