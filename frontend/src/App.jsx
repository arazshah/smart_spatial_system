import { useEffect, useMemo, useState } from "react";
import {
  createProject,
  getHealth,
  getMapLayers,
  getOutputManifest,
  getProject,
  getRequest,
  listProjects,
  listRequests,
  listUploads,
  runQuery,
  uploadFileByKind,
} from "./api/client";
import InspectorPanel from "./components/InspectorPanel";
import MapStage from "./components/MapStage";
import TopQueryBar from "./components/TopQueryBar";
import WorkbenchDrawer from "./components/WorkbenchDrawer";
import WorkbenchSidebar from "./components/WorkbenchSidebar";

function asList(payload, keys = []) {
  if (Array.isArray(payload)) return payload;

  for (const key of keys) {
    if (Array.isArray(payload?.[key])) return payload[key];
  }

  if (Array.isArray(payload?.items)) return payload.items;
  if (Array.isArray(payload?.data)) return payload.data;

  return [];
}

function normalizeResponseRecord(record) {
  if (!record) return null;
  return record.production_response || record.response || record.result || record;
}

function createRequestId() {
  return `req-ui-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
}

function normalizeKind(upload) {
  const value = String(upload?.kind || "").toLowerCase();

  if (value.includes("vector")) return "vector";
  if (value.includes("raster")) return "raster";

  return "raster";
}

function inferBandMapFromQuery(query) {
  const text = String(query || "").toLowerCase();

  if (
    text.includes("ndvi") ||
    text.includes("vegetation") ||
    text.includes("پوشش گیاهی") ||
    text.includes("گیاهی")
  ) {
    return {
      red: 1,
      nir: 2,
    };
  }

  return {};
}

export default function App() {
  const [health, setHealth] = useState(null);
  const [projects, setProjects] = useState([]);
  const [activeProject, setActiveProject] = useState(null);

  const [uploads, setUploads] = useState([]);
  const [selectedUpload, setSelectedUpload] = useState(null);

  const [requests, setRequests] = useState([]);
  const [activeRequest, setActiveRequest] = useState(null);

  const [response, setResponse] = useState(null);
  const [mapLayers, setMapLayers] = useState(null);
  const [outputManifest, setOutputManifest] = useState(null);

  const [activeTool, setActiveTool] = useState(null);
  const [loading, setLoading] = useState(false);
  const [bootLoading, setBootLoading] = useState(true);
  const [globalError, setGlobalError] = useState("");

  async function refreshProjects(preferredProjectId = null) {
    const payload = await listProjects();
    const items = asList(payload, ["projects"]);
    setProjects(items);

    const nextProjectId =
      preferredProjectId ||
      activeProject?.project_id ||
      items[0]?.project_id ||
      null;

    if (nextProjectId) {
      const full = await getProject(nextProjectId);
      setActiveProject(full);
    } else {
      setActiveProject(null);
    }
  }

  async function refreshUploads() {
    const payload = await listUploads();
    setUploads(asList(payload, ["uploads"]));
  }

  async function refreshRequests() {
    const payload = await listRequests();
    setRequests(asList(payload, ["requests"]));
  }

  async function bootstrap() {
    try {
      setBootLoading(true);
      setGlobalError("");

      const healthPayload = await getHealth();
      setHealth(healthPayload);

      await refreshProjects();
      await refreshUploads();
      await refreshRequests();
    } catch (err) {
      setGlobalError(err.message);
    } finally {
      setBootLoading(false);
    }
  }

  useEffect(() => {
    bootstrap();
  }, []);

  useEffect(() => {
    if (!activeProject?.uploads?.length) {
      setSelectedUpload(null);
      return;
    }

    if (
      selectedUpload?.upload_id &&
      !activeProject.uploads.includes(selectedUpload.upload_id)
    ) {
      setSelectedUpload(null);
    }
  }, [activeProject, selectedUpload]);

  function findBestUploadForActiveProject() {
    if (!activeProject?.uploads?.length) return null;

    if (
      selectedUpload?.upload_id &&
      activeProject.uploads.includes(selectedUpload.upload_id)
    ) {
      return selectedUpload;
    }

    const projectUploadIds = new Set(activeProject.uploads);

    return uploads.find((item) => projectUploadIds.has(item.upload_id)) || null;
  }

  function buildSmartPayload(command) {
    if (!activeProject?.project_id) {
      throw new Error("ابتدا از منوی Projects یک پروژه بسازید یا انتخاب کنید.");
    }

    const bestUpload = findBestUploadForActiveProject();
    const kind = normalizeKind(bestUpload);

    const inputs = bestUpload
      ? kind === "vector"
        ? { vector_ref: bestUpload.upload_id }
        : { raster_ref: bestUpload.upload_id }
      : {};

    return {
      query: command.query,
      request_id: createRequestId(),
      inputs,
      band_map: inferBandMapFromQuery(command.query),
      metadata: {
        project_id: activeProject.project_id,
        selected_upload_id: bestUpload?.upload_id || null,
        intelligent_input_selection: true,
      },
      user_context: {
        source: "minimal-intelligent-workbench",
        input_selection_mode: bestUpload ? "auto_project_data" : "no_explicit_data",
      },
    };
  }

  async function handleCreateProject(payload) {
    const created = await createProject(payload);
    await refreshProjects(created.project_id);
    setActiveTool(null);
    return created;
  }

  async function handleSelectProject(project) {
    const full = await getProject(project.project_id);
    setActiveProject(full);
    setSelectedUpload(null);
    setActiveRequest(null);
    setResponse(null);
    setMapLayers(null);
    setOutputManifest(null);
  }

  async function handleUploadFile(kind, file) {
    if (!activeProject?.project_id) {
      throw new Error("ابتدا پروژه فعال انتخاب کنید.");
    }

    const uploaded = await uploadFileByKind(kind, file, activeProject.project_id);

    await refreshUploads();

    const full = await getProject(activeProject.project_id);
    setActiveProject(full);
    setSelectedUpload(uploaded);

    return uploaded;
  }

  async function loadRequestWorkspace(requestId) {
    const record = await getRequest(requestId);
    const normalized = normalizeResponseRecord(record);

    setActiveRequest(record);
    setResponse(normalized);

    try {
      const [layers, outputs] = await Promise.all([
        getMapLayers(requestId),
        getOutputManifest(requestId),
      ]);

      setMapLayers(layers);
      setOutputManifest(outputs);
    } catch {
      setMapLayers(null);
      setOutputManifest(null);
    }
  }

  async function handleSelectRequest(request) {
    await loadRequestWorkspace(request.request_id);
    setActiveTool(null);
  }

  async function handleRunQuery(command) {
    try {
      setLoading(true);
      setGlobalError("");
      setResponse(null);
      setMapLayers(null);
      setOutputManifest(null);

      const payload = buildSmartPayload(command);
      const result = await runQuery(payload);

      setResponse(result);
      setActiveRequest(result);

      if (result?.request_id) {
        try {
          const [layers, outputs] = await Promise.all([
            getMapLayers(result.request_id),
            getOutputManifest(result.request_id),
          ]);

          setMapLayers(layers);
          setOutputManifest(outputs);
        } catch {
          setMapLayers(null);
          setOutputManifest(null);
        }
      }

      await refreshRequests();

      if (payload?.metadata?.project_id) {
        const full = await getProject(payload.metadata.project_id);
        setActiveProject(full);
      }
    } catch (err) {
      setGlobalError(err.message);
      setResponse({
        status: "failed",
        answer: err.message,
      });
    } finally {
      setLoading(false);
    }
  }

  const outputFiles = useMemo(() => {
    return outputManifest?.files || [];
  }, [outputManifest]);

  if (bootLoading) {
    return <div className="wb-loader">در حال آماده‌سازی محیط کاری...</div>;
  }

  return (
    <div className="workbench-shell">
      <WorkbenchSidebar
        activeTool={activeTool}
        onSelectTool={setActiveTool}
      />

      <WorkbenchDrawer
        activeTool={activeTool}
        onClose={() => setActiveTool(null)}
        projects={projects}
        activeProject={activeProject}
        onCreateProject={handleCreateProject}
        onSelectProject={handleSelectProject}
        uploads={uploads}
        selectedUpload={selectedUpload}
        onSelectUpload={(upload) => {
          setSelectedUpload(upload);
          setActiveTool(null);
        }}
        onUploadFile={handleUploadFile}
        requests={requests}
        activeRequest={activeRequest}
        onSelectRequest={handleSelectRequest}
        health={health}
      />

      <main className="workbench-main">
        {globalError && <div className="global-toast error">{globalError}</div>}

        <TopQueryBar
          project={activeProject}
          uploads={uploads}
          selectedUpload={selectedUpload}
          onRun={handleRunQuery}
          loading={loading}
          onOpenProjects={() => setActiveTool("projects")}
          onOpenUploads={() => setActiveTool("uploads")}
        />

        <MapStage
          project={activeProject}
          selectedUpload={selectedUpload}
          mapLayers={mapLayers}
          loading={loading}
        />
      </main>

      <InspectorPanel
        response={response}
        mapLayers={mapLayers}
        outputFiles={outputFiles}
        activeRequest={activeRequest}
      />
    </div>
  );
}
