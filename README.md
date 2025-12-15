# Historian: A Subsequent Edit Recommendation Evaluation Framework

**Historian** is a standardized evaluation framework for next-edit recommendation systems.


## 🎯 What is This?

Git commit are snapshot with edit's temporal order lost. This framework reconstructs the natural editing sequence from commits and evaluates next-edit recommendation systems in a realistic, step-by-step manner. This framework:

1. **Recovers the natural editing order** from commits by analyzing code dependencies and structural relationships
2. **Reconstructs the developer's context** at each editing step, replaying the codebase state as edits are applied one by one
3. **Evaluates next-edit recommendation systems** by simulating the real development process and measuring if predicted edits match what the developer actually did next

### Why This Matters

Traditional evaluation approaches test code generation in isolation. We test whether a system can **predict what a developer will edit next** given the realistic, incremental context they actually had during development. This tests both:
- **What** to edit (location accuracy)
- **When** to edit it (respecting the natural flow and dependencies)


## Simulation Flow

```
┌────────────────────────────────────────────────────────────────┐
│ Phase 1: Initialization                                        │
├────────────────────────────────────────────────────────────────┤
│ 1. Extract edits from given commit                             │
│ 2. Build partial order between edits                           │
│ 3. Initialize SUT (models, LSP servers, etc.)                  │
│ 4. Select & apply initial edit → Establish baseline state      │
└────────────────────────────────────────────────────────────────┘
                            ↓
┌────────────────────────────────────────────────────────────────┐
│ Phase 2: Iterative Recommendation (Loop until all edits done)  │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ Step A: Request full recommendation:                    │   │
│  │         SUT.subsequent_edit_recommendation(context)     │   │
│  │             → locations + contents                      │   │
│  └─────────────────────────────────────────────────────────┘   │
│                            ↓                                   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ Step B: Evaluate against ground truth                   │   │
│  │         Match criteria: 50% line overlap + BLEU-4 > 50  │   │
│  └─────────────────────────────────────────────────────────┘   │
│                            ↓                                   │
│            ┌───────────────┴───────────────┐                   │
│            │                               │                   │
│      ✅ Match Found              ❌ No Match Found             │
│            │                               │                   │
│            ↓                               ↓                   │
│  ┌──────────────────────┐  ┌───────────────────────────────┐   │
│  │ Select matched edit  │  │ Step C: Fallback mode         │   │
│  │ Record: precision    │  │ • Pick a GT location          │   │
│  │         recall       │  │ • Request content for GT loc  │   │
│  │         F1-score     │  │   SUT.generate_edit_solution()│   │
│  │         ...          │  │       → contents              │   │              
│  └──────────────────────┘  └───────────────────────────────┘   │
│            │                                │                  │
│            └────────────┬───────────────────┘                  │
│                         ↓                                      │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ Step D: Apply selected edit to codebase                 │   │
│  │         Update project state                            │   │
│  └─────────────────────────────────────────────────────────┘   │
│                         │ More edits?                          │
│                         ├───────────┐                          │
│                     No  │           │ Yes → Loop back          │
└─────────────────────────┴───────────┴──→───────────────────────┘
                          │ No
                          ↓
┌────────────────────────────────────────────────────────────────┐
│ Phase 3: Reporting                                             │
├────────────────────────────────────────────────────────────────┤
│ • Aggregate metrics (precision, recall, BLEU, latency)         │
│ • Flow pattern statistics (keeping/jumping/breaking/reverting/)│
│ • Save to JSON: {project}-{sha}-{method}-results.json          │
└────────────────────────────────────────────────────────────────┘
```

**Key Features:**
- **Dual-mode evaluation**: Full recommendation (location + content) with fallback to content-only
- **Realistic context**: Each prediction uses the exact codebase state at that editing step
- **Flow-aware**: Tracks whether recommendations respect edit dependencies


## 📊 Evaluation Metrics

A predicted edit is considered matching a ground truth edit if:
- **Location Overlap**: At least 50% line overlap with a ground truth edit
- **Content Similarity**: BLEU-4 score between predicted and ground truth edit content greater than 50.

Based on this, we compute the following metrics:

### 1. Flow Pattern Analysis

Categorizes predictions based on dependency ordering:

- **`flow_keeping`** ✅: Correct prediction that is one-hop from applied edits
- **`flow_jumping`** ⚠️: Correct prediction that is multi-hop from applied edits
- **`flow_breaking`** ❌: Wrong prediction that matches no ground truth edits
- **`flow_reverting`** 🔄: Prediction that undoes a previous edit

### 2. Traditional Retrieval Metrics

```python
precision@all = flow_keeping / total_predictions
recall@all    = flow_keeping / allowed_ground_truth_edits
f1_score@all  = 2 * precision * recall / (precision + recall)
tp@k          = number of correct predictions in top-k
```

### 3. Content Quality

- **BLEU-4**: Measures similarity between predicted and ground truth code

### 4. Performance

- **Latency**: Time taken for a single subsequent_edit_recommendation request (seconds)


## 🏗️ Framework architecture

```
┌─────────────────────────────────────────────────────────────┐
│  simulation/                   Evaluation Framework         │
│  ├─ main.py                    Simulation orchestrator      │
│  ├─ commit.py                  Commit & edit state manager  │
│  ├─ utils.py                   Git parsing, BLEU scoring    │
│  ├─ edit_dependency.py         Dependency analysis          │
│  ├─ partial_order.py           Edit ordering recovery       │
│  └─ testset.json               Test commits dataset         │
└─────────────────────────────────────────────────────────────┘
                              ↓ provides API
┌─────────────────────────────────────────────────────────────┐
│  systemUnderTest/              Your Method Implementation   │
│  ├─ TRACE/                     Reference implementation     │
│  │   ├─ main.py                Entry point (required API)   │
│  │   ├─ TRACE.py               Logic-based recommendation   │
│  │   ├─ Invoker.py             Strategy selector            │
│  │   ├─ Locator.py             Location prediction model    │
│  │   └─ Generator.py           Content generation model     │
│  │                                                          │
│  └─ YourMethod/                👈 Implement your method     │
│      └─ main.py                Must implement required API  │
└─────────────────────────────────────────────────────────────┘
                              ↓ uses
┌─────────────────────────────────────────────────────────────┐
│  libs/                         Tool Libraries               │
│  ├─ LSPs/                      Language Server Protocol     │
│  │   ├─ language_server.py     Base LSP client              │
│  │   ├─ py_lsp.py              Python (Pyright)             │
│  │   ├─ java_lsp.py            Java                         │
│  │   └─ go_lsp.py              Go                           │
│  │                                                          │
│  └─ tree-sitter/               Code parsing & AST analysis  │
└─────────────────────────────────────────────────────────────┘
```


## Framework environment Setup

```bash
# Create conda environment
conda create -n historian python=3.12
conda activate historian

# Install dependencies
pip install -r requirements.txt
```

## Run TRACE as baseline

1. Install language servers:

    ```bash
    # Pyright is installed via `requirements.txt`
    # jdt.ls are provided in `libs/LSPs/jdt-language-server/`

    # TypeScript/JavaScript
    npm install -g typescript-language-server typescript

    # Go
    go install golang.org/x/tools/gopls@latest
    ```

2. Download TRACE model checkpoints:

    ```bash
    cd systemUnderTest/TRACE
    bash download_models.sh
    cd ../../
    ```

3. Setup configurations in `.env`:

    ```bash
    SUT=TRACE  # system under test
    EVAL_SET= # default is `simulation/testset.json`
    REPOS_DIR= # path to clone all simulated repositories
    OUTPUT_DIR= # path to save simulation results
    FLOW_ANALYSIS= # whether to enable flow pattern analysis (true/false)

    # TRACE specific configurations
    INVOKER_MODEL_PATH= # path to TRACE invoker model checkpoint
    LOCATOR_MODEL_PATH= # path to TRACE locator model checkpoint
    GENERATOR_MODEL_PATH= # path to TRACE generator model checkpoint
    DEVICE= # device for model inference (e.g., cpu, cuda:0)

    # If you need to evaluate flow patterns:
    OPENAI_API_KEY= # your OpenAI API key
    OPENAI_BASE_URL= # your OpenAI base URL (if any)
    ```

4. Run simulation:

    ```bash
    # Evaluate TRACE method on Python commits
    python -m simulation.main

    # Results will be saved to:
    # output/{project}-{commit_sha}-{method}-simulation-results.json
    ```


## Implement Your Own Method

1. Implement the required API in `systemUnderTest/YourMethod/main.py`:

    ```python
    def setup(json_input):
        # Initialize your method (load models, start LSP, etc.)

    def subsequent_edit_recommendation(json_input):
        # Given current project state, return predicted edits in snapshots format

    def generate_edit_solution(json_input):
        # Generate edit solution for a given location

    def end(json_input):
        # Clean up resources (close LSP, free memory, etc.)
    ```

2. Setup configurations in `.env`:

    ```bash
    SUT=YourMethod  # system under test
    EVAL_SET= # default is `simulation/testset.json`
    REPOS_DIR= # path to clone all simulated repositories
    OUTPUT_DIR= # path to save simulation results
    FLOW_ANALYSIS= # whether to enable flow pattern analysis (true/false)

    # YourMethod specific configurations
    XXX=XXX  # add your method specific configs here

    # If you need to evaluate flow patterns:
    OPENAI_API_KEY= # your OpenAI API key
    OPENAI_BASE_URL= # your OpenAI base URL (if any)
    ```

3. Run simulation:

    ```bash
    # Evaluate TRACE method on Python commits
    python -m simulation.main

    # Results will be saved to:
    # output/{project}-{commit_sha}-{method}-simulation-results.json
    ```


## 🤝 Contributing

We welcome implementations of new edit recommendation methods!

### Adding a New Method

1. Create `systemUnderTest/YourMethod/main.py`
2. Implement required API (see "Implement Your Own Method")
3. Add test commits to `simulation/testset.json`
4. Run evaluation and submit results

### Improving the Framework

- Better dependency analysis algorithms
- Additional evaluation metrics
- Support for more languages
- Performance optimizations



## 📞 Contact

- Email: chenyan@u.nus.edu

---

**Happy Evaluating! 🚀**
