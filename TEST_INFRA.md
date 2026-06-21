# ALS Genetics Explorer - Test Infrastructure Plan (TEST_INFRA.md)

This document outlines the testing infrastructure, directory layout, commands, and rules for the ALS Genetic Mechanism Explorer test suite.

## 1. Testing Philosophy
We utilize a **requirement-driven, opaque-box (black-box) testing architecture** structured into four distinct tiers. No external network connections are permitted during test execution. All external biological API endpoints are intercepted using pytest mocks.

---

## 2. Test Directory Layout

Tests are grouped by tier to maintain clear separation of concerns:

```text
tests/
├── TEST_INFRA.md           # This document
├── conftest.py             # Global fixtures (requests mocking, db initializers)
├── tier1_coverage/         # Tier 1: Happy path requirements
│   ├── test_config.py
│   ├── test_cache.py
│   ├── test_offline.py
│   ├── test_ingest_log.py
│   ├── test_db_schema.py
│   ├── test_graph.py
│   ├── test_scoring.py
│   ├── test_hypotheses.py
│   ├── test_dashboard.py
│   └── test_literature.py
├── tier2_boundary/         # Tier 2: Boundary and corner cases
│   ├── test_config_edge.py
│   ├── test_cache_edge.py
│   ├── test_offline_edge.py
│   ├── test_ingest_log_edge.py
│   ├── test_db_schema_edge.py
│   ├── test_graph_edge.py
│   ├── test_scoring_edge.py
│   ├── test_hypotheses_edge.py
│   ├── test_dashboard_edge.py
│   └── test_literature_edge.py
├── tier3_combination/      # Tier 3: Pairwise combination integration tests
│   └── test_combinations.py
└── tier4_scenarios/        # Tier 4: Real-world workflow end-to-end tests
    ├── test_pipeline_workflow.py
    └── test_dashboard_interactive.py
```

---

## 3. Setup and Requirements

The test suite requires the packages listed in `requirements.txt`.

Ensure your virtual environment is active and up to date:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Verify that pytest is correctly installed:
```bash
python -m pytest --version
```

---

## 4. How to Run the Tests

To run the complete test suite:
```bash
pytest -v
```

### Running Specific Tiers
You can run specific test folders or files using standard pytest selectors:

- **Run Tier 1 Only**:
  ```bash
  pytest -v tests/tier1_coverage/
  ```

- **Run Tier 2 Only**:
  ```bash
  pytest -v tests/tier2_boundary/
  ```

- **Run Tier 3 Only**:
  ```bash
  pytest -v tests/tier3_combination/
  ```

- **Run Tier 4 Only**:
  ```bash
  pytest -v tests/tier4_scenarios/
  ```

### Verifying Hermetic Network Execution
The mock interceptor automatically raises an `OfflineNetworkViolation` if any live network requests are attempted during a test. To verify that no tests are sneaking requests past the socket layer, you can run tests with mock validation enabled.

---

## 5. Adding New Tests

When adding new tests:
1. **Identify the Target Feature**: Use the `FEAT-XXX` codes defined in `analysis.md`.
2. **Select the Correct Tier**:
   - Happy path verification goes to `tests/tier1_coverage/`.
   - Edge/boundary conditions (empty databases, bad inputs, timeouts) go to `tests/tier2_boundary/`.
   - Feature integrations go to `tests/tier3_combination/`.
   - User workflow/scenarios go to `tests/tier4_scenarios/`.
3. **Use Mock Payloads**: Do not write custom HTTP interceptors in individual tests. Always leverage the global fixtures in `tests/conftest.py`. If you require a new mock payload, append it to the `mock_api_database` fixture in `conftest.py`.
4. **Assert on Interfaces**: Do not assert on private variables or internal implementation details. Verify CLI exit codes, stdout outputs, database rows, or generated files.

## 6. Streamlit Dashboard Integration Testing
Streamlit dashboard tests in Tier 4 utilize `streamlit.testing.v1.AppTest`. This allows programmatic UI tests without spinning up a live web server or browser engine:

```python
from streamlit.testing.v1 import AppTest

def test_dashboard_slider_recalculation():
    # Load dashboard script
    at = AppTest.from_file("src/dashboard/app.py")
    at.run()
    
    # Assert initial UI state
    assert not at.exception
    
    # Interact with scoring sliders
    ot_slider = at.slider(key="weight_ot_score")
    ot_slider.set_value(0.9)
    at.run()
    
    # Verify that updated weights are reflected
    assert at.markdown[0].value.startswith("### Recalculating scores")
```
