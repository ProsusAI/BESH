#!/bin/bash

# BatchEndpoint 8-GPU Management Script

set -e

COMPOSE_FILE="docker-compose-8gpu.yml"
PROJECT_NAME="batchendpoint-8gpu"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Helper functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check prerequisites
check_prerequisites() {
    log_info "Checking prerequisites..."
    
    # Check Docker
    if ! command -v docker &> /dev/null; then
        log_error "Docker is not installed"
        exit 1
    fi
    
    # Check Docker Compose
    if ! command -v docker-compose &> /dev/null; then
        log_error "Docker Compose is not installed"
        exit 1
    fi
    
    # Check NVIDIA SMI
    if ! command -v nvidia-smi &> /dev/null; then
        log_warning "nvidia-smi not found - GPU monitoring unavailable"
    else
        # Check GPU count
        GPU_COUNT=$(nvidia-smi --query-gpu=count --format=csv,noheader,nounits | head -1)
        if [ "$GPU_COUNT" -lt 8 ]; then
            log_warning "Only $GPU_COUNT GPUs detected (8 required for optimal performance)"
        else
            log_success "$GPU_COUNT GPUs detected"
        fi
    fi
    
    # Check if compose file exists
    if [ ! -f "$COMPOSE_FILE" ]; then
        log_error "Docker compose file $COMPOSE_FILE not found"
        exit 1
    fi
    
    log_success "Prerequisites check completed"
}

# Start services
start_services() {
    log_info "Starting 8-GPU BatchEndpoint services..."
    docker-compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" up -d
    log_success "Services started"
    
    log_info "Waiting for services to become healthy..."
    sleep 10
    check_health
}

# Stop services
stop_services() {
    log_info "Stopping 8-GPU BatchEndpoint services..."
    docker-compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" down
    log_success "Services stopped"
}

# Restart services
restart_services() {
    log_info "Restarting 8-GPU BatchEndpoint services..."
    stop_services
    sleep 5
    start_services
}

# Check service health
check_health() {
    log_info "Checking service health..."
    
    # Check nginx load balancer
    if curl -s -f http://localhost:8000/health > /dev/null; then
        log_success "Nginx load balancer is healthy"
    else
        log_error "Nginx load balancer is not responding"
    fi
    
    # Check batch API
    if curl -s -f http://localhost:5000/ > /dev/null; then
        log_success "Batch API is healthy"
    else
        log_error "Batch API is not responding"
    fi
    
    # Check individual vLLM instances
    for i in {0..7}; do
        port=$((8001 + i))
        if curl -s -f http://localhost:$port/health > /dev/null; then
            log_success "vLLM GPU-$i (port $port) is healthy"
        else
            log_warning "vLLM GPU-$i (port $port) is not responding"
        fi
    done
}

# Show service status
show_status() {
    log_info "Service status:"
    docker-compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" ps
}

# Show logs
show_logs() {
    if [ -z "$1" ]; then
        log_info "Showing logs for all services..."
        docker-compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" logs -f --tail=50
    else
        log_info "Showing logs for service: $1"
        docker-compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" logs -f --tail=50 "$1"
    fi
}

# Monitor GPU usage
monitor_gpu() {
    if command -v nvidia-smi &> /dev/null; then
        log_info "Monitoring GPU usage (press Ctrl+C to stop)..."
        watch -n 2 nvidia-smi
    else
        log_error "nvidia-smi not available"
        exit 1
    fi
}

# Show container resource usage
show_stats() {
    log_info "Container resource usage:"
    docker stats $(docker-compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" ps -q)
}

# Test load balancer
test_load_balancer() {
    log_info "Testing load balancer distribution..."
    
    for i in {1..16}; do
        echo -n "Request $i: "
        response=$(curl -s -w "%{http_code}" -o /dev/null http://localhost:8000/health)
        if [ "$response" -eq 200 ]; then
            echo -e "${GREEN}OK${NC}"
        else
            echo -e "${RED}FAIL ($response)${NC}"
        fi
        sleep 0.5
    done
}

# Clean up (remove volumes)
cleanup() {
    log_warning "This will remove all data volumes. Are you sure? (y/N)"
    read -r response
    if [[ "$response" =~ ^[Yy]$ ]]; then
        log_info "Cleaning up volumes..."
        docker-compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" down -v
        log_success "Cleanup completed"
    else
        log_info "Cleanup cancelled"
    fi
}

# Show help
show_help() {
    echo "BatchEndpoint 8-GPU Management Script"
    echo ""
    echo "Usage: $0 [COMMAND]"
    echo ""
    echo "Commands:"
    echo "  start      Start all services"
    echo "  stop       Stop all services"
    echo "  restart    Restart all services"
    echo "  status     Show service status"
    echo "  health     Check service health"
    echo "  logs       Show logs for all services"
    echo "  logs SERVICE   Show logs for specific service"
    echo "  gpu        Monitor GPU usage"
    echo "  stats      Show container resource usage"
    echo "  test       Test load balancer"
    echo "  cleanup    Stop services and remove volumes"
    echo "  check      Check prerequisites"
    echo "  help       Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 start"
    echo "  $0 logs nginx-lb"
    echo "  $0 logs vllm-gpu-0"
}

# Main script logic
case "${1:-help}" in
    start)
        check_prerequisites
        start_services
        ;;
    stop)
        stop_services
        ;;
    restart)
        restart_services
        ;;
    status)
        show_status
        ;;
    health)
        check_health
        ;;
    logs)
        show_logs "$2"
        ;;
    gpu)
        monitor_gpu
        ;;
    stats)
        show_stats
        ;;
    test)
        test_load_balancer
        ;;
    cleanup)
        cleanup
        ;;
    check)
        check_prerequisites
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        log_error "Unknown command: $1"
        echo ""
        show_help
        exit 1
        ;;
esac
