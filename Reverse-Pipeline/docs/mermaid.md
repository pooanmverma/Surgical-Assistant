System Architecture: Surgical Specification Mining Pipeline
This diagram illustrates how raw video data is transformed into executable Python code through a multi-stage mathematical optimization process.

graph TD
    %% --- Data Ingestion Stage ---
    subgraph Stage1_Data_Ingestion [Stage 1: Trace Generation]
        VIDEO[("Ideal Surgery Video<br/>(.mp4)")] -->|Frame Input| YOLO[("YOLOv12x<br/>(Vision Engine)")]
        YOLO -->|Detections| ADAPTER[("Unified Adapter<br/>(DeepSORT + Logic)")]
        ADAPTER -->|Track IDs & State| LOGGER["trace_logger.py<br/>(Event Recorder)"]
        LOGGER -->|Generates| CSV[("ideal_surgery_trace.csv<br/>(Raw Event Log)")]
    end

    %% --- Mining Stage ---
    subgraph Stage2_Mining_Engine [Stage 2: STL Mining Engine]
        CSV -->|Input| LOADER["stl_mining/signals.py<br/>(Signal Processor)"]
        
        LOADER -->|Interpolation &<br/>Smoothing| TRACE_OBJ["SignalTrace Object<br/>(Continuous Math Signals)"]
        
        TEMPLATES["stl_mining/templates.py<br/>(Rule Grammar)"] -->|Defines Structure| OPTIMIZER
        
        TRACE_OBJ --> OPTIMIZER["stl_mining/optimizer.py<br/>(Monotonic Binary Search)"]
        
        MATH_CORE["stl_mining/core.py<br/>(Vectorized Robustness Math)"] -.->|Calculates Rho| OPTIMIZER
        
        OPTIMIZER -->|Iterative<br/>Optimization| PARAM_TUNING{"Robustness > 0?"}
        PARAM_TUNING -- No --> REJECT[Discard Rule]
        PARAM_TUNING -- Yes --> ACCEPT[Accept Parameter]
    end

    %% --- Code Generation Stage ---
    subgraph Stage3_Code_Synthesis [Stage 3: Specification Compiler]
        ACCEPT -->|Mined Parameters| GENERATOR["run_mining_pipeline.py<br/>(Code Generator)"]
        GENERATOR -->|Writes| JSON[("mined_constraints.json<br/>(Interchange Format)")]
        GENERATOR -->|Compiles| PY_CODE[("generated_policy.py<br/>(Executable Logic)")]
    end

    %% --- Runtime Integration ---
    subgraph Stage4_Deployment [Stage 4: Runtime Integration]
        PY_CODE -->|Imported By| COPILOT["unified_main_system.py<br/>(Surgical Copilot)"]
        COPILOT -->|Enforces| ALERTS[("Real-Time Safety Alerts")]
    end

    %% Styling
    style Stage1_Data_Ingestion fill:#e1f5fe,stroke:#01579b,stroke-width:2px
    style Stage2_Mining_Engine fill:#fff3e0,stroke:#e65100,stroke-width:2px
    style Stage3_Code_Synthesis fill:#e8f5e9,stroke:#1b5e20,stroke-width:2px
    style Stage4_Deployment fill:#f3e5f5,stroke:#4a148c,stroke-width:2px
    
    style MATH_CORE fill:#333,color:#fff
    style PY_CODE fill:#ffeb3b,stroke:#fbc02d,stroke-width:2px


Deep Dive: What Each File DoesStage 1: Trace Generationtrace_logger.py:Input: Your video20.mp4.Action: Runs the full CVS model (YOLO+Adapter) but without drawing boxes. Instead, it writes every event (e.g., "Frame 100: Clipper Active, Dissection=4.5s") to a CSV file.Why: We need a "Ground Truth" history to learn from.Stage 2: The Mining Engine (The "Brain")This is the stl_mining package you just built.stl_mining/signals.py:Action: Reads the CSV. It converts discrete events (like 1|4 tool strings) into continuous numpy arrays (0.0 to 1.0 signals). It applies smoothing to remove AI flicker so the math doesn't fail on noise.stl_mining/templates.py:Action: Defines the "Shapes" of rules. For example, it defines that a Precondition looks like Always(Trigger -> Timer > ?). It defines the Search Space (e.g., "Check timer values from 0s to 60s").stl_mining/core.py:Action: The low-level math kernel. It calculates Robustness ($\rho$). It takes a signal and a rule and returns a number: positive if the rule is safe, negative if violated. It uses Sliding Window algorithms for temporal operators.stl_mining/optimizer.py:Action: The Solver. It takes a Template (from templates.py) and the Trace (from signals.py). It uses Binary Search to find the strictest possible parameter that still results in a positive Robustness score.Example: It tries 10s (Fail), then 0s (Pass), then 5s (Pass), then 7.5s (Fail)... until it finds 4.8s is the limit.Stage 3: Code Synthesisrun_mining_pipeline.py:Action: The Orchestrator. It loads the modules, runs the optimizer for every template type (Safety, Sequence, Co-occurrence), and then writes Python code.Output: generated_policy.py. This is a Python class that didn't exist before—it was created by the machine.Stage 4: DeploymentIntegration: You simply import the generated class into your main system. Now your AI is enforcing rules it learned by itself.