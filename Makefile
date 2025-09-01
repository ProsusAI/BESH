# BESH - Batch Evaluation System with HuggingFace
# Makefile for Docker Compose management with automatic .env loading

# Include .env file if it exists
-include .env
export

# Set default values
ENV_FILE := .env
ENV_EXAMPLE := env.example
COMPOSE_FILE := docker-compose.yml
COMPOSE_8GPU_FILE := docker-compose-8gpu.yml
COMPOSE_TEST_FILE := docker-compose.test.yml

# Colors for output
RED := \033[0;31m
GREEN := \033[0;32m
YELLOW := \033[0;33m
BLUE := \033[0;34m
NC := \033[0m # No Color

# Default target
.PHONY: help
help: ## Show this help message
	@echo "${BLUE}BESH - Batch Evaluation System with HuggingFace${NC}"
	@echo "${BLUE}================================================${NC}"
	@echo ""
	@echo "Available commands:"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "${GREEN}%-20s${NC} %s\n", $$1, $$2}'
	@echo ""
	@echo "${YELLOW}Environment files:${NC}"
	@echo "  .env           - Main environment file (auto-created from env.example)"
	@echo "  env.example    - Template environment file"
	@echo ""
	@echo "${YELLOW}Docker Compose files:${NC}"
	@echo "  docker-compose.yml       - Single GPU setup"
	@echo "  docker-compose-8gpu.yml  - Multi-GPU setup (8 GPUs)"
	@echo "  docker-compose.test.yml  - Testing setup"

# Environment file management
.PHONY: check-env
check-env: ## Check if .env file exists and create from template if needed
	@if [ ! -f $(ENV_FILE) ]; then \
		echo "${YELLOW}Warning: $(ENV_FILE) not found. Creating from $(ENV_EXAMPLE)...${NC}"; \
		if [ -f $(ENV_EXAMPLE) ]; then \
			cp $(ENV_EXAMPLE) $(ENV_FILE); \
			echo "${GREEN}✓ Created $(ENV_FILE) from $(ENV_EXAMPLE)${NC}"; \
			echo "${YELLOW}⚠ Please edit $(ENV_FILE) and set your actual values!${NC}"; \
		else \
			echo "${RED}✗ Error: $(ENV_EXAMPLE) not found!${NC}"; \
			exit 1; \
		fi \
	else \
		echo "${GREEN}✓ $(ENV_FILE) found${NC}"; \
	fi

.PHONY: validate-env
validate-env: check-env ## Validate required environment variables
	@echo "${BLUE}Validating environment variables...${NC}"
	@missing=0; \
	for var in MODEL_NAME HUGGING_FACE_HUB_TOKEN; do \
		if [ -z "$$(grep "^$$var=" $(ENV_FILE) | cut -d'=' -f2- | sed 's/^<your_token>//' | sed 's/^your_.*_here//' | tr -d ' ')" ]; then \
			echo "${RED}✗ Missing or placeholder value for $$var${NC}"; \
			missing=1; \
		else \
			echo "${GREEN}✓ $$var is set${NC}"; \
		fi \
	done; \
	if [ $$missing -eq 1 ]; then \
		echo "${YELLOW}⚠ Please update $(ENV_FILE) with your actual values${NC}"; \
		exit 1; \
	fi
	@echo "${GREEN}✓ Environment validation passed${NC}"

# Single GPU Docker Compose commands
.PHONY: up
up: check-env ## Start services with single GPU setup
	@echo "${BLUE}Starting single GPU setup...${NC}"
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_FILE) up -d
	@echo "${GREEN}✓ Services started. Check status with 'make status'${NC}"

.PHONY: down
down: ## Stop and remove all containers
	@echo "${BLUE}Stopping services...${NC}"
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_FILE) down
	@echo "${GREEN}✓ Services stopped${NC}"

.PHONY: build
build: check-env ## Build or rebuild services
	@echo "${BLUE}Building services...${NC}"
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_FILE) build --no-cache
	@echo "${GREEN}✓ Build completed${NC}"

.PHONY: rebuild
rebuild: down build up ## Full rebuild: stop, build, and start services

# 8-GPU Docker Compose commands
.PHONY: up-8gpu
up-8gpu: validate-env ## Start services with 8 GPU setup
	@echo "${BLUE}Starting 8 GPU setup with load balancer...${NC}"
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_8GPU_FILE) up -d
	@echo "${GREEN}✓ 8 GPU services started. Check status with 'make status-8gpu'${NC}"

.PHONY: down-8gpu
down-8gpu: ## Stop and remove 8 GPU setup containers
	@echo "${BLUE}Stopping 8 GPU services...${NC}"
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_8GPU_FILE) down
	@echo "${GREEN}✓ 8 GPU services stopped${NC}"

.PHONY: build-8gpu
build-8gpu: check-env ## Build or rebuild 8 GPU services
	@echo "${BLUE}Building 8 GPU services...${NC}"
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_8GPU_FILE) build --no-cache
	@echo "${GREEN}✓ 8 GPU build completed${NC}"

.PHONY: rebuild-8gpu
rebuild-8gpu: down-8gpu build-8gpu up-8gpu ## Full rebuild: stop, build, and start 8 GPU services

# Testing commands
.PHONY: test
test: check-env ## Run tests
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_TEST_FILE) up --build ## --abort-on-container-exit
	docker compose -f $(COMPOSE_TEST_FILE) down
	@echo "${GREEN}✓ Tests completed${NC}"

.PHONY: test-build
test-build: check-env ## Build test containers
	@echo "${BLUE}Building test containers...${NC}"
	docker compose -f $(COMPOSE_TEST_FILE) build --no-cache
	@echo "${GREEN}✓ Test build completed${NC}"

# Status and monitoring commands
.PHONY: status
status: ## Show status of single GPU services
	@echo "${BLUE}Single GPU Service Status:${NC}"
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_FILE) ps

.PHONY: status-8gpu
status-8gpu: ## Show status of 8 GPU services
	@echo "${BLUE}8 GPU Service Status:${NC}"
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_8GPU_FILE) ps

.PHONY: logs
logs: ## Show logs for single GPU setup
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_FILE) logs -f

.PHONY: logs-8gpu
logs-8gpu: ## Show logs for 8 GPU setup
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_8GPU_FILE) logs -f

.PHONY: logs-api
logs-api: ## Show logs for batch API service only
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_FILE) logs -f batch-api

.PHONY: logs-vllm
logs-vllm: ## Show logs for vLLM service only
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_FILE) logs -f vllm

# Health check commands
.PHONY: health
health: ## Check health of all services
	@echo "${BLUE}Health Check - Single GPU Setup:${NC}"
	@echo "API Health:"
	@curl -s http://localhost:5000/health 2>/dev/null && echo "${GREEN}✓ API is healthy${NC}" || echo "${RED}✗ API is not responding${NC}"
	@echo "vLLM Health:"
	@curl -s http://localhost:8000/health 2>/dev/null && echo "${GREEN}✓ vLLM is healthy${NC}" || echo "${RED}✗ vLLM is not responding${NC}"

.PHONY: health-8gpu
health-8gpu: ## Check health of 8 GPU services
	@echo "${BLUE}Health Check - 8 GPU Setup:${NC}"
	@echo "API Health:"
	@curl -s http://localhost:5000/health 2>/dev/null && echo "${GREEN}✓ API is healthy${NC}" || echo "${RED}✗ API is not responding${NC}"
	@echo "Load Balancer Health:"
	@curl -s http://localhost:8000/health 2>/dev/null && echo "${GREEN}✓ Load Balancer is healthy${NC}" || echo "${RED}✗ Load Balancer is not responding${NC}"

# Development commands
.PHONY: shell-api
shell-api: ## Open shell in batch-api container
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_FILE) exec batch-api /bin/bash

.PHONY: shell-vllm
shell-vllm: ## Open shell in vLLM container
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_FILE) exec vllm /bin/bash

# Cleanup commands
.PHONY: clean
clean: ## Remove stopped containers and unused images
	@echo "${BLUE}Cleaning up Docker resources...${NC}"
	docker system prune -f
	@echo "${GREEN}✓ Cleanup completed${NC}"

.PHONY: clean-all
clean-all: ## Remove all containers, images, and volumes (DESTRUCTIVE)
	@echo "${RED}⚠ This will remove ALL Docker resources including volumes!${NC}"
	@read -p "Are you sure? (y/N): " confirm && [ "$$confirm" = "y" ]
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_FILE) down -v
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_8GPU_FILE) down -v
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_TEST_FILE) down -v
	docker system prune -af --volumes
	@echo "${GREEN}✓ Complete cleanup finished${NC}"

# Backup commands
.PHONY: backup-db
backup-db: ## Backup the database
	@echo "${BLUE}Creating database backup...${NC}"
	@timestamp=$$(date +%Y%m%d_%H%M%S); \
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_FILE) exec batch-api cp /app/src/database/app.db /app/src/database/app_backup_$$timestamp.db && \
	echo "${GREEN}✓ Database backed up to app_backup_$$timestamp.db${NC}"

# Quick start command
.PHONY: quick-start
quick-start: validate-env up health ## Quick start: validate env, start services, and check health
	@echo "${GREEN}✓ BESH is ready!${NC}"
	@echo "${BLUE}Access the API at: http://localhost:5000${NC}"
	@echo "${BLUE}Access vLLM at: http://localhost:8000${NC}"

.PHONY: quick-start-8gpu
quick-start-8gpu: validate-env up-8gpu health-8gpu ## Quick start 8 GPU setup with validation and health check
	@echo "${GREEN}✓ BESH 8 GPU setup is ready!${NC}"
	@echo "${BLUE}Access the API at: http://localhost:5000${NC}"
	@echo "${BLUE}Access Load Balancer at: http://localhost:8000${NC}"

# Show environment variables
.PHONY: show-env
show-env: check-env ## Show current environment variables (sensitive values hidden)
	@echo "${BLUE}Current environment configuration:${NC}"
	@grep -v "^#" $(ENV_FILE) | grep -v "^$$" | sed 's/=.*/=***/' | sort
