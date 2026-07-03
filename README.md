
# BESH

![BESH Logo Banner](images/BESH_LOGO_BANNER.png)

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)


A high-performance batch processing API for large language models with support for both single-GPU and multi-GPU (8-GPU) deployments.

## Features

🚀 Intelligent Queue Management  
⚡ Advanced Parallel Processing  
🔄 Production-Ready Reliability  
📊 Real-Time Analytics Dashboard  
🎯 Enterprise-Scale Architecture  
💾 Persistent Storage  

## Quick Start

### Standard Deployment (Single GPU)

```bash
docker compose -f docker-compose.yml up -d --build --scale worker=4

# Access the dashboard
curl http://localhost:8080/
```

### High-Performance Deployment (24-GPU)

For high-throughput production workloads, use the 24-GPU configuration with load balancing:

```bash
export NGINX_CONF=nginx-24gpu.conf
export REMOTE_CLUSTER_HOST_A=<ip_remote_cluster>
export REMOTE_CLUSTER_HOST_B=<ip_remote_cluster>
docker compose -f docker-compose-multi-gpu.yml up -d --build --scale worker=12
```

## Screenshots

![BESH Dashboard Overview](images/Bashboard_overview_new.png)

Features:

- See throughput per 15min
- See uploads per 15min
- See stats per 24h
- See and delete individual batches

## Tips

Things you might want check:

- `MAX_WORKERS=128` & `MAX_CONCURRENT_BATCHES=10` in the docker compose files of the batch api.
  - if the batches are very large, maybe from concurrent batches, or visa versa.
  - Find your most efficient number of workers, I found 128 for H100 running a small model.
- `events {worker_connections 2048;}` Make sure this value is larger then MAX_WORKERS.
- Consider uploading the model once for faster init on 8 gpus.
- There is no storage managment system -> make sure you delete your batch files (in & out)


## 8-GPU Architecture Overview

The 8-GPU deployment provides horizontal scaling with the following architecture, featuring multiple workers and a Redis queue for efficient batch processing:

```mermaid
graph TB
    subgraph "Client Layer"
        Client[Client Applications]
    end
    
    subgraph "API Layer"
        BatchAPI[Batch API<br/>:8080]
    end
    
    subgraph "Queue Layer"
        Queue[Redis Queue<br/>:6379]
    end
    
    subgraph "Worker Layer"
        Worker1[Worker 1]
        Worker2[Worker 2]  
        Worker3[Worker 3]
        Worker4[Worker 4]
    end
    
    subgraph "Load Balancer Layer"
        LB[Nginx Load Balancer<br/>:8000]
    end
    
    subgraph "vLLM Inference Layer"
        GPU0[vLLM GPU-0<br/>Device: GPU 0]
        GPU1[vLLM GPU-1<br/>Device: GPU 1]
        GPU2[vLLM GPU-2<br/>Device: GPU 2]
        GPU3[vLLM GPU-3<br/>Device: GPU 3]
        GPU4[vLLM GPU-4<br/>Device: GPU 4]
        GPU5[vLLM GPU-5<br/>Device: GPU 5]
        GPU6[vLLM GPU-6<br/>Device: GPU 6]
        GPU7[vLLM GPU-7<br/>Device: GPU 7]
    end
    
    subgraph "Storage Layer"
        DB[(PostgreSQL<br/>Database)]
        Models[(Model Cache<br/>HuggingFace)]
        Files[(Batch Files<br/>Volume)]
    end
    
    Client --> BatchAPI
    BatchAPI --> Queue
    BatchAPI --> DB
    BatchAPI --> Files
    
    Queue --> Worker1
    Queue --> Worker2
    Queue --> Worker3
    Queue --> Worker4
    
    Worker1 --> LB
    Worker2 --> LB
    Worker3 --> LB
    Worker4 --> LB
    
    LB --> GPU0
    LB --> GPU1
    LB --> GPU2
    LB --> GPU3
    LB --> GPU4
    LB --> GPU5
    LB --> GPU6
    LB --> GPU7
    
    GPU0 --> Models
    GPU1 --> Models
    GPU2 --> Models
    GPU3 --> Models
    GPU4 --> Models
    GPU5 --> Models
    GPU6 --> Models
    GPU7 --> Models
    
    classDef gpu fill:#e1f5fe
    classDef lb fill:#f3e5f5
    classDef api fill:#e8f5e8
    classDef storage fill:#fff3e0
    classDef worker fill:#e8f5e8
    classDef queue fill:#fff9c4
    
    class GPU0,GPU1,GPU2,GPU3,GPU4,GPU5,GPU6,GPU7 gpu
    class LB lb
    class BatchAPI api
    class DB,Models,Files storage
    class Worker1,Worker2,Worker3,Worker4 worker
    class Queue queue
```

### Scaling Workers

To run with multiple workers for increased throughput, use the `--scale` option:

```bash
# Run with 4 workers (recommended for 8-GPU setup)
docker-compose -f docker-compose-multi-gpu.yml up --scale worker=4

# Or with 12 workers for maximum throughput
docker-compose -f docker-compose-multi-gpu.yml up --scale worker=12
```

The worker scaling provides:
- **Horizontal scaling**: Each worker processes batch jobs independently
- **Queue-based distribution**: Redis queue distributes jobs across available workers
- **Load balancing**: Workers share the load across all 8 GPU instances
- **Fault tolerance**: If a worker fails, other workers continue processing

## Options

### Test Model Inference

```bash
# Test vLLM endpoint directly
curl http://localhost:8000/v1/completions \
    -H "Content-Type: application/json" \
    -d '{
        "model": "<your_model_name>",           
        "prompt": "Why is open source important for the progress of AI?",
        "max_tokens": 100,
        "temperature": 0.3
    }'

# Test batch API health
curl http://localhost:8080/health
```

### Pytest

Run individual endpoint tests + 100 calls to openai gpt-nano. We do not have a pytest for GPUs. We advise running the `test_large.py` and `test_api.py` manually to check GPU deployment. Since vLLM is openai compatible, we did not see the need for those test.

```bash
# MAKE SURE YOU HAVE TEST_API_KEY set in .env
docker-compose -f docker-compose.test.yml up --build --scale worker=2
```

### Helper files

- [`scripts/check_batch.py`](scripts/check_batch.py) – CLI check batch from ID
- [`scripts/delete_x.py`](scripts/delete_x.py) – CLI delete files and/or Bacthes

### CI/CD

Recommended to only update the batch-api using this command for CI/CD pipelines.

```bash
docker compose -f <compose-file> up -d --no-deps --build batch-api
```

## Contribute

Contributions are welcome! Feel free to open an issue or submit a pull request.



---

## License

Copyright © 2025 MIH AI B.V.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
