//! Thin pyo3 shim over `aa-sdk-client`.
//!
//! Per ADR 0002 (Epic AAASM-2552), the runtime-client logic — UDS transport,
//! IPC wire codec, and the `AssemblyClient` lifecycle — lives once in the
//! shared `aa-sdk-client` crate. This binding is a thin pyo3 shim: ergonomic
//! API, type translation, and event capture. It holds **no** security
//! authority: the advisory, best-effort credential preflight is provided by
//! `aa-sdk-client`, and `aa-runtime` re-scans every event authoritatively.
//!
//! Approval is server-side (see the ADR trust model), so the binding does not
//! perform a synchronous approval round-trip; event reporting is
//! fire-and-forget over the shared client. The binding does expose a
//! synchronous, **advisory** `query_policy` over the same client: it asks the
//! runtime for a decision. The SDK layer is advisory — the runtime / proxy /
//! eBPF layers remain authoritative — but when the runtime is unreachable or
//! slow this binding does **not** fabricate an `allow`. It instead returns an
//! error-sentinel decision (`query_failed` / `channel_closed`) so the Python
//! layer's enforcement-mode guard can fail *closed* (deny) under `enforce`
//! rather than being silently downgraded to allow-on-failure (AAASM-3920).

use aa_core::AuditEntry;
use aa_proto::assembly::audit::v1::AuditEvent;
use aa_proto::assembly::audit::v1::CallStackNode as ProtoCallStackNode;
use aa_proto::assembly::common::v1::ActionType;
use aa_proto::assembly::common::v1::AgentId;
use aa_proto::assembly::common::v1::Decision;
use aa_proto::assembly::policy::v1::action_context::Action as ProtoAction;
use aa_proto::assembly::policy::v1::{ActionContext, CheckActionRequest, ToolCallContext};
use aa_sdk_client::ipc::spawn_ipc_thread;
use aa_sdk_client::{AssemblyClient, AssemblyConfig, SdkClientError};
use prost::Message;
use pyo3::exceptions::PyRuntimeError;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyAny, PyBytes, PyDict, PyList, PyModule};

#[pyclass(module = "agent_assembly._core", from_py_object)]
#[derive(Clone)]
struct GovernanceEvent {
    #[pyo3(get)]
    payload_json: String,
    audit_entry: AuditEntry,
}

#[pymethods]
impl GovernanceEvent {
    #[new]
    fn new(payload_json: String) -> PyResult<Self> {
        let audit_entry = serde_json::from_str::<AuditEntry>(&payload_json).map_err(|error| {
            PyValueError::new_err(format!(
                "GovernanceEvent payload must be serialized aa_core::AuditEntry JSON: {error}"
            ))
        })?;
        Ok(Self {
            payload_json,
            audit_entry,
        })
    }
}

/// Handle to an Agent Assembly runtime session.
///
/// Thin wrapper over [`aa_sdk_client::AssemblyClient`]: the UDS transport, wire
/// codec, and background IPC thread all live in `aa-sdk-client`. This type only
/// translates the Python surface (`connect` / `send_event` / `close`) onto the
/// shared client.
#[pyclass(module = "agent_assembly._core")]
struct RuntimeClient {
    #[pyo3(get)]
    socket_path: String,
    client: AssemblyClient,
}

#[pymethods]
impl RuntimeClient {
    /// Connect to `aa-runtime` over the given Unix domain socket path.
    ///
    /// Spawns the shared client's background IPC thread and returns a handle.
    /// Event reporting is fire-and-forget; a failed connection surfaces on a
    /// later `send_event` rather than here.
    ///
    /// `agent_id` is the agent identity the background thread signs the runtime
    /// session handshake with (AAASM-3587); pass `None` only when no identity is
    /// available yet.
    ///
    /// `sdk_version` is the user-facing PyPI package version (`agent_assembly.__version__`)
    /// the Python layer forwards so it — not the shared `aa-sdk-client` crate
    /// version — is what gets signed into the handshake (AAASM-3683). `None`
    /// lets the shared client fall back to the crate version (no regression vs
    /// AAASM-3666).
    #[staticmethod]
    #[pyo3(signature = (socket_path, agent_id=None, sdk_version=None))]
    fn connect(
        socket_path: String,
        agent_id: Option<String>,
        sdk_version: Option<String>,
    ) -> PyResult<Self> {
        let config = AssemblyConfig {
            agent_id: agent_id.unwrap_or_default(),
            socket_path: Some(socket_path.clone()),
            // Registration is a separate explicit `register` call; connecting
            // the runtime UDS does not need a gateway endpoint or lineage.
            gateway_endpoint: None,
            team_id: None,
            parent_agent_id: None,
            sdk_version,
            // No explicit override surfaced through this shim yet; falls back to
            // AASM_STATE_DIR / ~/.aasm/identity, matching this shim's pre-AAASM-5332
            // ambient-resolution behavior (AAASM-5839).
            identity_dir: None,
            org_id: None,
            registration_key: None,
        };
        let handle = spawn_ipc_thread(
            config.resolve_socket_path(),
            config.agent_id.clone(),
            config.resolved_sdk_version(),
        )
        .map_err(|error| {
            PyRuntimeError::new_err(format!("failed to start runtime IPC thread: {error}"))
        })?;
        Ok(Self {
            socket_path,
            client: AssemblyClient::new(handle, Vec::new()),
        })
    }

    /// Register this agent with the governance gateway and store the issued
    /// credential token.
    ///
    /// Delegates to [`aa_sdk_client::AssemblyClient::register`], the SDK's only
    /// direct gateway gRPC call (ADR 0004): it hits `AgentLifecycleService.Register`,
    /// and the returned `credential_token` is held by the shared client and
    /// attached to every later [`query_policy`](Self::query_policy) so the
    /// gateway's `validate_credential_token` does not deny a registered agent.
    ///
    /// `gateway_endpoint` is the gRPC endpoint (e.g. `"http://127.0.0.1:50051"`);
    /// pass `None` to let the shared client resolve it from the environment or
    /// its default. Returns the policy id the gateway assigns this agent.
    ///
    /// `team_id` and `parent_agent_id` carry the agent's lineage/team scoping to
    /// the gateway on the native register (AAASM-3415): `team_id` drives
    /// team-budget attribution and `parent_agent_id` the topology graph. Both
    /// are optional — `None` leaves the agent team-unscoped / root.
    ///
    /// `org_id` is a claim and `registration_key` the operator API key the
    /// gateway binds to an organization (AAASM-6444); see
    /// [`aa_sdk_client::AssemblyConfig::org_id`].
    ///
    /// Raises `RuntimeError` when the gateway is unreachable or rejects the
    /// registration — unlike `query_policy`, registration is not advisory, so a
    /// failure surfaces rather than failing open.
    // Each parameter is a distinct optional Python kwarg crossing the PyO3
    // boundary; bundling them into a struct would force callers to build one in
    // Python, complicating the call site for no benefit.
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (agent_id, name, framework, gateway_endpoint=None, team_id=None, parent_agent_id=None, org_id=None, registration_key=None))]
    fn register(
        &self,
        py: Python<'_>,
        agent_id: String,
        name: String,
        framework: String,
        gateway_endpoint: Option<String>,
        team_id: Option<String>,
        parent_agent_id: Option<String>,
        org_id: Option<String>,
        registration_key: Option<String>,
    ) -> PyResult<String> {
        let config = AssemblyConfig {
            agent_id,
            socket_path: Some(self.socket_path.clone()),
            gateway_endpoint,
            team_id,
            parent_agent_id,
            // The version is signed at IPC-handshake time (`connect`), not on the
            // gateway register, so it is not needed for this config.
            sdk_version: None,
            // See the `connect` config above (AAASM-5839).
            identity_dir: None,
            // AAASM-6444: the org is a claim the gateway validates against the
            // key; both are optional and absent means an org-less registration.
            org_id,
            registration_key: registration_key.map(aa_sdk_client::RegistrationKey::new),
        };
        // `register` is async (tonic). Release the GIL and drive the future on a
        // private current-thread runtime so the blocking gRPC round-trip never
        // stalls other Python threads.
        py.detach(move || {
            let runtime = tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build()
                .map_err(|error| {
                    PyRuntimeError::new_err(format!("failed to build register runtime: {error}"))
                })?;
            runtime
                .block_on(self.client.register(&config, name, framework))
                .map_err(map_sdk_error)
        })
    }

    /// Ship a captured governance event to the runtime (fire-and-forget).
    ///
    /// The event payload passes through `aa-sdk-client`'s advisory preflight
    /// before the wire; the runtime re-scans authoritatively regardless.
    fn send_event(&self, py: Python<'_>, event: GovernanceEvent) -> PyResult<()> {
        let event_type = format!("{:?}", event.audit_entry.event_type());
        let details = event.payload_json;
        // Release the GIL while delegating: aa-sdk-client uses a bounded channel
        // with a blocking send, so under backpressure this can park the calling
        // thread — holding the GIL there would stall every other Python thread.
        py.detach(move || self.client.report_event(event_type, details))
            .map_err(map_sdk_error)
    }

    /// Synchronously ask the runtime for a policy decision on an action.
    ///
    /// Builds a `CheckActionRequest` from the supplied identity and action
    /// fields, ships it over the shared client, and blocks for the runtime's
    /// `CheckActionResponse` (the shared client caps this at a 5 s timeout).
    /// Returns a dict `{"decision": <str>, "reason": <str>}` where the decision
    /// is one of `"allow"`, `"deny"`, `"pending"`, `"redact"`, `"unspecified"`,
    /// or — when the runtime could not produce a verdict — an error sentinel
    /// (`"query_failed"` / `"channel_closed"`).
    ///
    /// **Fail-closed signalling (AAASM-3920):** the SDK layer is advisory, never
    /// authoritative, but it must not fabricate an `allow` on failure. When the
    /// runtime is unreachable or does not answer in time — the shared client
    /// returns [`SdkClientError::QueryFailed`] on timeout / mid-query
    /// disconnect, or [`SdkClientError::ChannelClosed`] when the IPC connection
    /// never came up (e.g. the runtime socket is absent) — this returns the
    /// matching error-sentinel decision (`"query_failed"` / `"channel_closed"`),
    /// **not** `"allow"`. The Python `runtime_interceptor` guard then denies
    /// under `enforce` (fail closed) and proceeds under `observe` / `disabled`
    /// (fail open). Folding these onto `allow` here would make that guard dead
    /// code and let a stalled / killed runtime silently downgrade enforcement.
    /// The runtime / proxy / eBPF layers still enforce authoritatively.
    #[pyo3(signature = (agent_id, action_type, tool_name=None, tool_args_json=None))]
    fn query_policy(
        &self,
        py: Python<'_>,
        agent_id: String,
        action_type: String,
        tool_name: Option<String>,
        tool_args_json: Option<String>,
    ) -> PyResult<Py<PyAny>> {
        let context = tool_name.map(|name| ActionContext {
            action: Some(ProtoAction::ToolCall(ToolCallContext {
                tool_name: name,
                args_json: tool_args_json.unwrap_or_default().into_bytes(),
                ..Default::default()
            })),
        });
        let request = CheckActionRequest {
            agent_id: Some(AgentId {
                org_id: String::new(),
                team_id: String::new(),
                agent_id,
            }),
            action_type: action_type_from_str(&action_type),
            context,
            ..Default::default()
        };

        // Release the GIL while blocking on the runtime round-trip so other
        // Python threads keep running during the (up to 5 s) wait.
        let outcome = py.detach(move || self.client.query_policy(request));

        let response = match outcome {
            Ok(response) => response,
            // Fail-closed signalling (AAASM-3920): a query the runtime could not
            // answer must surface as an error-sentinel decision, NOT a
            // fabricated allow, so the Python guard denies under enforce.
            // QueryFailed = timeout / mid-query disconnect; ChannelClosed = the
            // IPC connection never came up. Emit the sentinel string the
            // `runtime_interceptor._ERROR_DECISIONS` guard matches and return
            // directly (these are not proto `Decision` variants).
            Err(error @ (SdkClientError::QueryFailed | SdkClientError::ChannelClosed)) => {
                let decision = match error {
                    SdkClientError::ChannelClosed => "channel_closed",
                    _ => "query_failed",
                };
                let result = PyDict::new(py);
                result.set_item("decision", decision)?;
                result.set_item(
                    "reason",
                    "runtime unreachable; failing closed under enforce",
                )?;
                return Ok(result.into_any().unbind());
            }
            Err(error) => return Err(map_sdk_error(error)),
        };

        let result = PyDict::new(py);
        result.set_item("decision", policy_decision_to_str(response.decision))?;
        result.set_item("reason", response.reason)?;
        Ok(result.into_any().unbind())
    }

    /// Shut down the background IPC thread. Idempotent.
    fn close(&self, py: Python<'_>) {
        // Release the GIL while the shared client joins the background thread.
        py.detach(|| {
            let _ = self.client.shutdown();
        });
    }
}

fn map_sdk_error(error: SdkClientError) -> PyErr {
    PyRuntimeError::new_err(error.to_string())
}

fn action_type_from_str(value: &str) -> i32 {
    match value {
        "llm_call" => ActionType::LlmCall as i32,
        "tool_call" => ActionType::ToolCall as i32,
        "file_op" | "file_operation" => ActionType::FileOperation as i32,
        "network_call" => ActionType::NetworkCall as i32,
        "process_exec" => ActionType::ProcessExec as i32,
        "agent_spawn" => ActionType::AgentSpawn as i32,
        "tool_result" => ActionType::ToolResult as i32,
        _ => ActionType::ActionUnspecified as i32,
    }
}

fn action_type_to_str(value: i32) -> &'static str {
    match ActionType::try_from(value).unwrap_or(ActionType::ActionUnspecified) {
        ActionType::LlmCall => "llm_call",
        ActionType::ToolCall => "tool_call",
        ActionType::FileOperation => "file_op",
        ActionType::NetworkCall => "network_call",
        ActionType::ProcessExec => "process_exec",
        ActionType::AgentSpawn => "agent_spawn",
        ActionType::ToolResult => "tool_result",
        ActionType::ActionUnspecified => "",
    }
}

fn decision_from_str(value: &str) -> i32 {
    match value {
        "allow" => Decision::Allow as i32,
        "deny" => Decision::Deny as i32,
        "pending" => Decision::Pending as i32,
        "redact" => Decision::Redact as i32,
        _ => Decision::Unspecified as i32,
    }
}

fn audit_event_from_py(event: &Bound<'_, PyAny>) -> PyResult<AuditEvent> {
    let event_id = event.getattr("event_id")?.extract::<String>()?;
    let agent_id_str = event.getattr("agent_id")?.extract::<String>()?;
    let action_type_str = event.getattr("action_type")?.extract::<String>()?;
    let decision_str = event.getattr("decision")?.extract::<String>()?;
    let trace_id = event.getattr("trace_id")?.extract::<String>()?;
    let span_id = event.getattr("span_id")?.extract::<String>()?;
    let parent_span_id = event.getattr("parent_span_id")?.extract::<String>()?;
    let labels = event
        .getattr("labels")?
        .extract::<std::collections::HashMap<String, String>>()?;
    let call_stack_py = event.getattr("call_stack")?;
    let mut call_stack = Vec::new();
    for node in call_stack_py.try_iter()? {
        call_stack.push(call_stack_node_from_py(&node?)?);
    }
    Ok(AuditEvent {
        event_id,
        agent_id: Some(AgentId {
            org_id: String::new(),
            team_id: String::new(),
            agent_id: agent_id_str,
        }),
        action_type: action_type_from_str(&action_type_str),
        decision: decision_from_str(&decision_str),
        trace_id,
        span_id,
        parent_span_id,
        labels,
        call_stack,
        ..Default::default()
    })
}

fn audit_event_to_py(py: Python<'_>, event: &AuditEvent) -> PyResult<Py<PyAny>> {
    let types_module = PyModule::import(py, "agent_assembly.types")?;
    let cls = types_module.getattr("AuditEvent")?;
    let kwargs = PyDict::new(py);
    kwargs.set_item("event_id", &event.event_id)?;
    let agent_id_str = event
        .agent_id
        .as_ref()
        .map(|id| id.agent_id.clone())
        .unwrap_or_default();
    kwargs.set_item("agent_id", agent_id_str)?;
    kwargs.set_item("action_type", action_type_to_str(event.action_type))?;
    kwargs.set_item("decision", decision_to_str(event.decision))?;
    kwargs.set_item("trace_id", &event.trace_id)?;
    kwargs.set_item("span_id", &event.span_id)?;
    kwargs.set_item("parent_span_id", &event.parent_span_id)?;
    let labels = PyDict::new(py);
    for (k, v) in &event.labels {
        labels.set_item(k, v)?;
    }
    kwargs.set_item("labels", labels)?;
    let call_stack = PyList::empty(py);
    for node in &event.call_stack {
        call_stack.append(call_stack_node_to_py(py, node)?)?;
    }
    kwargs.set_item("call_stack", call_stack)?;
    Ok(cls.call((), Some(&kwargs))?.unbind())
}

fn call_stack_node_from_py(node: &Bound<'_, PyAny>) -> PyResult<ProtoCallStackNode> {
    let id = node.getattr("id")?.extract::<String>()?;
    let kind = node.getattr("kind")?.extract::<String>()?;
    let label = node.getattr("label")?.extract::<String>()?;
    let latency_ms = node
        .getattr("latency_ms")?
        .extract::<Option<i64>>()?
        .unwrap_or(0);
    let children_py = node.getattr("children")?;
    let mut children = Vec::new();
    for child in children_py.try_iter()? {
        children.push(call_stack_node_from_py(&child?)?);
    }
    Ok(ProtoCallStackNode {
        id,
        kind,
        label,
        latency_ms,
        children,
    })
}

fn call_stack_node_to_py(py: Python<'_>, node: &ProtoCallStackNode) -> PyResult<Py<PyAny>> {
    let types_module = PyModule::import(py, "agent_assembly.types")?;
    let cls = types_module.getattr("CallStackNode")?;
    let kwargs = PyDict::new(py);
    kwargs.set_item("id", &node.id)?;
    kwargs.set_item("kind", &node.kind)?;
    kwargs.set_item("label", &node.label)?;
    let latency: Option<i64> = if node.latency_ms == 0 {
        None
    } else {
        Some(node.latency_ms)
    };
    kwargs.set_item("latency_ms", latency)?;
    let children = PyList::empty(py);
    for child in &node.children {
        children.append(call_stack_node_to_py(py, child)?)?;
    }
    kwargs.set_item("children", children)?;
    Ok(cls.call((), Some(&kwargs))?.unbind())
}

fn decision_to_str(value: i32) -> &'static str {
    match Decision::try_from(value).unwrap_or(Decision::Unspecified) {
        Decision::Allow => "allow",
        Decision::Deny => "deny",
        Decision::Pending => "pending",
        Decision::Redact => "redact",
        Decision::Unspecified => "",
    }
}

/// Map a policy `Decision` to the string surfaced by `query_policy`.
///
/// Unlike [`decision_to_str`] (which collapses the unspecified case to the
/// empty string for the audit-event surface), a policy verdict always names its
/// decision — an unspecified verdict becomes `"unspecified"`.
fn policy_decision_to_str(value: i32) -> &'static str {
    match Decision::try_from(value).unwrap_or(Decision::Unspecified) {
        Decision::Allow => "allow",
        Decision::Deny => "deny",
        Decision::Pending => "pending",
        Decision::Redact => "redact",
        Decision::Unspecified => "unspecified",
    }
}

#[pyfunction]
fn audit_event_to_wire_bytes(py: Python<'_>, event: &Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
    let proto = audit_event_from_py(event)?;
    let encoded = proto.encode_to_vec();
    Ok(PyBytes::new(py, &encoded).into_any().unbind())
}

#[pyfunction]
fn audit_event_from_wire_bytes(py: Python<'_>, data: &Bound<'_, PyBytes>) -> PyResult<Py<PyAny>> {
    let proto = AuditEvent::decode(data.as_bytes()).map_err(|error| {
        PyValueError::new_err(format!("failed to decode AuditEvent wire bytes: {error}"))
    })?;
    audit_event_to_py(py, &proto)
}

#[pymodule]
fn _core(_py: Python<'_>, module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<GovernanceEvent>()?;
    module.add_class::<RuntimeClient>()?;
    module.add_function(wrap_pyfunction!(audit_event_to_wire_bytes, module)?)?;
    module.add_function(wrap_pyfunction!(audit_event_from_wire_bytes, module)?)?;
    Ok(())
}
