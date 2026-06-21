import "./App.css";
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
  listProjectDataSources,
  previewDataSource,
  updateDataSource,
  deleteDataSource,
  registerPostGISSource,
  registerWFSSource,
  registerURLSource,
  registerCSVTableSource,
  registerWMSSource,
} from "./api/client";
import InspectorPanel from "./components/InspectorPanel";
import MapStage from "./components/MapStage";
import TopQueryBar from "./components/TopQueryBar";
import WorkbenchDrawer from "./components/WorkbenchDrawer";
import WorkbenchSidebar from "./components/WorkbenchSidebar";
import {
  getDataSourceBBox,
  getDataSourceCrs,
  getDataSourceCrsLabel,
  getDataSourceFeatureCount,
  getDataSourceGeometryType,
  getDataSourceKind,
  getDataSourceName,
  getDataSourcePropertyKeys,
  getDataSourceSize,
  getDataSourceStatus,
  getDataSourceTime,
  normalizeDataSource,
  normalizeDataSourceList,
} from "./lib/dataSources";
import Modal from "./components/Modal";
import DataSourcePreviewMap from "./components/DataSourcePreviewMap";
import { extractInlineGeoJsonLayers, mergeMapLayerPayloads } from "./utils/geojsonLayers";

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


function formatPreviewBytes(value) {
  const bytes = Number(value || 0);

  if (!Number.isFinite(bytes) || bytes <= 0) return "—";

  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = bytes;
  let unitIndex = 0;

  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }

  return `${size.toFixed(size >= 10 || unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
}

function formatPreviewDateTime(value) {
  if (!value) return "—";

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) return "—";

  return new Intl.DateTimeFormat("fa-IR", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(date);
}

function getPreviewPropertyKeys(payload) {
  return getDataSourcePropertyKeys(payload);
}

function getPreviewFeatureCount(payload) {
  return getDataSourceFeatureCount(payload);
}

function getPreviewCrs(payload) {
  return getDataSourceCrsLabel(payload) || "—";
}

function getPreviewBBox(payload) {
  const value = getDataSourceBBox(payload);

  if (!value) return "—";
  if (Array.isArray(value)) return value.join(", ");
  return String(value);
}

function getPreviewCrsLabel(payload) {
  return getDataSourceCrsLabel(payload);
}

function getPreviewRasterShape(payload) {
  const width =
    payload?.preview?.width ??
    payload?.metadata?.width ??
    payload?.summary?.width ??
    null;

  const height =
    payload?.preview?.height ??
    payload?.metadata?.height ??
    payload?.summary?.height ??
    null;

  if (width && height) return `${width} × ${height}`;
  if (width) return `${width}`;
  if (height) return `${height}`;
  return "—";
}

function getPreviewBandCount(payload) {
  return (
    payload?.preview?.band_count ??
    payload?.metadata?.band_count ??
    payload?.summary?.band_count ??
    payload?.bands ??
    null
  );
}


function normalizeHistoryStatus(value) {
  const status = String(value || "").toLowerCase();

  if (["succeeded", "success", "completed", "done"].includes(status)) {
    return "succeeded";
  }

  if (["failed", "error"].includes(status)) {
    return "failed";
  }

  if (["running", "pending", "analyzing"].includes(status)) {
    return "running";
  }

  return status || "unknown";
}

function makeRequestRecord(result, payload, command, project) {
  const requestId =
    result?.request_id ||
    result?.id ||
    payload?.request_id ||
    command?.request_id ||
    null;

  if (!requestId) return null;

  const query =
    command?.query ||
    payload?.query ||
    payload?.question ||
    result?.query ||
    result?.metadata?.original_query ||
    result?.request?.query ||
    "";

  const rawStatus =
    result?.status ||
    result?.run_result?.status ||
    (result?.ok === true ? "succeeded" : result?.ok === false ? "failed" : "");

  const status = normalizeHistoryStatus(rawStatus);

  return {
    ...(typeof result === "object" && result ? result : {}),
    request_id: requestId,
    project_id: project?.project_id || result?.project_id || payload?.project_id || null,
    query,
    question: query,
    status,
    created_at:
      result?.created_at ||
      result?.timestamp ||
      result?.metadata?.created_at ||
      new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };
}

function upsertRequestById(items, record) {
  if (!record?.request_id) return items || [];

  const list = Array.isArray(items) ? items : [];
  const index = list.findIndex((item) => item.request_id === record.request_id);

  if (index === -1) {
    return [record, ...list];
  }

  const next = [...list];
  next[index] = {
    ...next[index],
    ...record,
  };

  return next;
}

function attachRequestToProject(project, requestId) {
  if (!project?.project_id || !requestId) return project;

  const requests = Array.isArray(project.requests) ? project.requests : [];

  if (requests.includes(requestId)) {
    return project;
  }

  return {
    ...project,
    requests: [requestId, ...requests],
  };
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

  const [layerWorkspace, setLayerWorkspace] = useState({
    hiddenLayerKeys: new Set(),
    removedLayerKeys: new Set(),
    layerStyles: {},
    fitRequest: { key: null, featureId: null, trigger: 0 },
    styleLayerKey: null,
    selectedLayerKey: null,
    selectedFeatureId: null,
    selectedFeatureProperties: null,
    selectedTableId: null,
  });

  const [activeTool, setActiveTool] = useState(null);
  const [loading, setLoading] = useState(false);
  const [bootLoading, setBootLoading] = useState(true);
  const [globalError, setGlobalError] = useState("");

  const [previewModalData, setPreviewModalData] = useState(null);
  const [editModalUpload, setEditModalUpload] = useState(null);
  const [deleteModalUpload, setDeleteModalUpload] = useState(null);

  // ── DSM: Add External Source Modal ──────────────────────────
  const [dsmModalOpen, setDsmModalOpen] = useState(false);
  const [dsmTab, setDsmTab] = useState("postgis"); // "postgis" | "wfs" | "url"
  const [dsmBusy, setDsmBusy] = useState(false);
  const [dsmError, setDsmError] = useState("");

  // PostGIS form
  const [dsmPGHost, setDsmPGHost] = useState("");
  const [dsmPGPort, setDsmPGPort] = useState("5432");
  const [dsmPGDatabase, setDsmPGDatabase] = useState("");
  const [dsmPGUser, setDsmPGUser] = useState("");
  const [dsmPGPassword, setDsmPGPassword] = useState("");
  const [dsmPGSchema, setDsmPGSchema] = useState("public");
  const [dsmPGTable, setDsmPGTable] = useState("");
  const [dsmPGWhere, setDsmPGWhere] = useState("");
  const [dsmPGLimit, setDsmPGLimit] = useState("1000");
  const [dsmPGName, setDsmPGName] = useState("");

  // WFS form
  const [dsmWFSUrl, setDsmWFSUrl] = useState("");
  const [dsmWFSTypeName, setDsmWFSTypeName] = useState("");
  const [dsmWFSVersion, setDsmWFSVersion] = useState("2.0.0");
  const [dsmWFSMaxFeatures, setDsmWFSMaxFeatures] = useState("1000");
  const [dsmWFSName, setDsmWFSName] = useState("");

  // URL form
  const [dsmURLValue, setDsmURLValue] = useState("");
  const [dsmURLName, setDsmURLName] = useState("");
  const [dsmURLKind, setDsmURLKind] = useState("vector");
  
  const [dsmCSVName, setDsmCSVName] = useState("");
  const [dsmCSVUrl, setDsmCSVUrl] = useState("");
  const [dsmCSVTableName, setDsmCSVTableName] = useState("");
  const [dsmCSVXColumn, setDsmCSVXColumn] = useState("longitude");
  const [dsmCSVYColumn, setDsmCSVYColumn] = useState("latitude");
  const [dsmCSVDelimiter, setDsmCSVDelimiter] = useState(",");
  const [dsmCSVEncoding, setDsmCSVEncoding] = useState("utf-8");
  const [dsmCSVCrs, setDsmCSVCrs] = useState("EPSG:4326");
  const [dsmCSVHasHeader, setDsmCSVHasHeader] = useState(true);

  const [dsmWMSName, setDsmWMSName] = useState("");
  const [dsmWMSUrl, setDsmWMSUrl] = useState("");
  const [dsmWMSLayer, setDsmWMSLayer] = useState("");
  const [dsmWMSVersion, setDsmWMSVersion] = useState("1.3.0");
  const [dsmWMSFormat, setDsmWMSFormat] = useState("image/png");
  const [dsmWMSCrs, setDsmWMSCrs] = useState("EPSG:3857");
  const [dsmWMSTransparent, setDsmWMSTransparent] = useState(true);
  const [dsmWMSAttribution, setDsmWMSAttribution] = useState("");
  const [dsmWMSOpacity, setDsmWMSOpacity] = useState("0.85");
const [dsmUploadKind, setDsmUploadKind] = useState("vector");
  const [dsmUploadFile, setDsmUploadFile] = useState(null);

  const [editName, setEditName] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [editTags, setEditTags] = useState("");

  const [modalBusy, setModalBusy] = useState(false);
  const [modalError, setModalError] = useState("");

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

  async function refreshUploads(projectOverride = null) {
    const projectId = projectOverride?.project_id || activeProject?.project_id;

    if (projectId) {
      const payload = await listProjectDataSources(projectId);
      setUploads(normalizeDataSourceList(asList(payload, ["uploads"])));
      return;
    }

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

      setMapLayers((previous) => mergeMapLayerPayloads(previous, layers));
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

      const pendingRecord = makeRequestRecord(
        {
          request_id: payload.request_id,
          status: "running",
        },
        payload,
        command,
        activeProject,
      );

      if (pendingRecord?.request_id) {
        setActiveRequest(pendingRecord);
        setRequests((previous) => upsertRequestById(previous, pendingRecord));
        setActiveProject((previous) =>
          attachRequestToProject(previous, pendingRecord.request_id),
        );
      }

      const result = await runQuery(payload);
      const inlineLayers = extractInlineGeoJsonLayers(result);

      if (inlineLayers.layers.length) {
        setMapLayers((previous) => mergeMapLayerPayloads(inlineLayers, previous));
      }

      const completedRecord = makeRequestRecord(
        result,
        payload,
        command,
        activeProject,
      );

      setResponse(result);
      setActiveRequest(completedRecord || result);

      if (completedRecord?.request_id) {
        setRequests((previous) => upsertRequestById(previous, completedRecord));
        setActiveProject((previous) =>
          attachRequestToProject(previous, completedRecord.request_id),
        );
      }

      if (result?.request_id) {
        try {
          const [layers, outputs] = await Promise.all([
            getMapLayers(result.request_id),
            getOutputManifest(result.request_id),
          ]);

          setMapLayers((previous) => mergeMapLayerPayloads(previous, layers));
          setOutputManifest(outputs);
        } catch {
          // Keep inline GeoJSON layers extracted from the main /query response.
          // /map-layers or /outputs may not exist for direct handlers.
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


  async function handlePreviewUpload(upload) {
    if (!upload?.upload_id) return null;

    setActiveTool(null);
    setModalError("");
    setModalBusy(true);

    try {
      const payload = await previewDataSource(upload.upload_id);
      const merged = normalizeDataSource({
        ...upload,
        ...payload,
        preview: payload?.preview || upload?.preview,
      });

      setUploads((prev) =>
        prev.map((item) =>
          item?.upload_id === upload.upload_id ? merged : item
        )
      );

      setSelectedUpload((prev) =>
        prev?.upload_id === upload.upload_id ? merged : prev
      );

      setPreviewModalData(merged);
      return merged;
    } catch (err) {
      setModalError(err.message);
      throw err;
    } finally {
      setModalBusy(false);
    }
  }

  async function handleEditUpload(upload) {
    if (!upload?.upload_id) return null;

    setActiveTool(null);
    setModalError("");
    setEditModalUpload(upload);
    setEditName(
      upload?.display_name ||
      upload?.name ||
      upload?.filename ||
      ""
    );
    setEditDescription(upload?.description || "");
    setEditTags(Array.isArray(upload?.tags) ? upload.tags.join(", ") : "");
    return upload;
  }

  async function handleSaveEditModal() {
    if (!editModalUpload?.upload_id) return null;

    const trimmedName = String(editName || "").trim();

    if (!trimmedName) {
      setModalError("نام منبع داده نمی‌تواند خالی باشد.");
      return null;
    }

    setModalError("");
    setModalBusy(true);

    try {
      const tags = String(editTags || "")
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean);

      const updated = await updateDataSource(editModalUpload.upload_id, {
        name: trimmedName,
        description: String(editDescription || "").trim(),
        tags,
      });

      await refreshUploads();

      setSelectedUpload((prev) =>
        prev?.upload_id === editModalUpload.upload_id ? updated : prev
      );

      setPreviewModalData((prev) =>
        prev?.upload_id === editModalUpload.upload_id
          ? { ...prev, ...updated }
          : prev
      );

      setEditModalUpload(null);
      setEditName("");
      setEditDescription("");
      setEditTags("");

      return updated;
    } catch (err) {
      setModalError(err.message);
      throw err;
    } finally {
      setModalBusy(false);
    }
  }

  async function handleDeleteUpload(upload) {
    if (!upload?.upload_id) return null;

    setActiveTool(null);
    setModalError("");
    setDeleteModalUpload(upload);
    return upload;
  }

  // ── DSM Handlers ────────────────────────────────────────────

  function resetDsmForms() {
    setDsmPGHost(""); setDsmPGPort("5432"); setDsmPGDatabase("");
    setDsmPGUser(""); setDsmPGPassword(""); setDsmPGSchema("public");
    setDsmPGTable(""); setDsmPGWhere(""); setDsmPGLimit("1000"); setDsmPGName("");
    setDsmWFSUrl(""); setDsmWFSTypeName(""); setDsmWFSVersion("2.0.0");
    setDsmWFSMaxFeatures("1000"); setDsmWFSName("");
    setDsmURLValue(""); setDsmURLName(""); setDsmURLKind("vector");
    setDsmCSVName(""); setDsmCSVUrl(""); setDsmCSVTableName("");
    setDsmCSVXColumn("longitude"); setDsmCSVYColumn("latitude");
    setDsmCSVDelimiter(","); setDsmCSVEncoding("utf-8"); setDsmCSVCrs("EPSG:4326");
    setDsmCSVHasHeader(true);
    setDsmWMSName(""); setDsmWMSUrl(""); setDsmWMSLayer("");
    setDsmWMSVersion("1.3.0"); setDsmWMSFormat("image/png"); setDsmWMSCrs("EPSG:3857");
    setDsmWMSTransparent(true); setDsmWMSAttribution(""); setDsmWMSOpacity("0.85");
    setDsmError("");
  }

  function openDsmModal(tab = "postgis") {
    resetDsmForms();
    setDsmTab(tab);
    setDsmModalOpen(true);
  }

  function getDsmUploadProjectId() {
    const candidates = [];

    if (typeof activeProject !== "undefined" && activeProject) candidates.push(activeProject);
    if (typeof selectedProject !== "undefined" && selectedProject) candidates.push(selectedProject);
    if (typeof currentProject !== "undefined" && currentProject) candidates.push(currentProject);

    const project = candidates.find(Boolean);
    if (project?.id) return project.id;
    if (project?.project_id) return project.project_id;

    if (typeof activeProjectId !== "undefined" && activeProjectId) return activeProjectId;
    if (typeof selectedProjectId !== "undefined" && selectedProjectId) return selectedProjectId;
    if (typeof currentProjectId !== "undefined" && currentProjectId) return currentProjectId;

    return undefined;
  }

  async function refreshAfterDsmFileUpload(uploadedSource = null) {
    const normalizedSource =
      uploadedSource?.data_source ||
      uploadedSource?.source ||
      uploadedSource?.upload ||
      uploadedSource?.data ||
      uploadedSource;

    const projectId =
      activeProject?.project_id ||
      activeProject?.id ||
      getDsmUploadProjectId();

    let refreshed = false;

    if (projectId) {
      try {
        const payload = await listProjectDataSources(projectId);
        const nextUploads = normalizeDataSourceList(
          asList(payload, ["uploads", "data_sources", "dataSources", "items", "results"])
        );

        setUploads(nextUploads);
        refreshed = true;
      } catch (err) {
        console.warn("DSM listProjectDataSources refresh failed:", err);
      }

      try {
        const full = await getProject(projectId);
        setActiveProject(full);
        setProjects((prev) =>
          Array.isArray(prev)
            ? prev.map((project) =>
                project.project_id === projectId || project.id === projectId ? full : project
              )
            : prev
        );
      } catch (err) {
        console.warn("DSM getProject refresh failed:", err);
      }
    }

    if (
      !refreshed &&
      normalizedSource &&
      typeof setUploads === "function" &&
      (normalizedSource.upload_id || normalizedSource.id || normalizedSource.data_source_id)
    ) {
      setUploads((prev) => {
        const current = Array.isArray(prev) ? prev : [];
        const sourceId =
          normalizedSource.upload_id ||
          normalizedSource.id ||
          normalizedSource.data_source_id;

        const filtered = current.filter((item) => {
          const itemId = item.upload_id || item.id || item.data_source_id;
          return itemId !== sourceId;
        });

        return [normalizedSource, ...filtered];
      });
    }

    window.dispatchEvent(
      new CustomEvent("dsm:data-source-uploaded", {
        detail: { uploadedSource: normalizedSource, projectId },
      })
    );

    window.dispatchEvent(
      new CustomEvent("smart-spatial:data-sources-changed", {
        detail: { uploadedSource: normalizedSource, projectId },
      })
    );

    window.dispatchEvent(
      new CustomEvent("smart-spatial:refresh-data-sources", {
        detail: { uploadedSource: normalizedSource, projectId },
      })
    );
  }

  async function handleDsmFileUpload() {
    if (!dsmUploadFile) {
      setDsmError("لطفاً یک فایل برای آپلود انتخاب کنید.");
      return;
    }

    setDsmBusy(true);
    setDsmError("");

    try {
      const { uploadRaster, uploadVector } = await import("./api/client");
      const projectId = getDsmUploadProjectId();

      let uploadedSource = null;

      if (dsmUploadKind === "raster") {
        uploadedSource = await uploadRaster(dsmUploadFile, projectId);
      } else {
        uploadedSource = await uploadVector(dsmUploadFile, projectId);
      }

      setDsmUploadFile(null);
      await refreshAfterDsmFileUpload(uploadedSource);
      setDsmModalOpen(false);
    } catch (err) {
      setDsmError(err?.message || "آپلود فایل با خطا مواجه شد.");
    } finally {
      setDsmBusy(false);
    }
  }

  async function handleDsmSubmit() {
    setDsmError("");
    setDsmBusy(true);

    try {
      let result = null;

      if (dsmTab === "postgis") {
        if (!dsmPGTable.trim()) throw new Error("نام جدول الزامی است.");
        if (!dsmPGHost.trim() && !dsmPGDatabase.trim())
          throw new Error("host و database الزامی هستند.");

        result = await registerPostGISSource({
          project_id: activeProject?.project_id || getDsmUploadProjectId() || null,
          display_name: dsmPGName.trim() || dsmPGTable.trim(),
          host: dsmPGHost.trim() || undefined,
          port: dsmPGPort ? parseInt(dsmPGPort) : undefined,
          database: dsmPGDatabase.trim() || undefined,
          user: dsmPGUser.trim() || undefined,
          password: dsmPGPassword || undefined,
          schema: dsmPGSchema.trim() || "public",
          table: dsmPGTable.trim(),
          where: dsmPGWhere.trim() || undefined,
          limit: dsmPGLimit ? parseInt(dsmPGLimit) : 1000,
        });
      } else if (dsmTab === "wfs") {
        if (!dsmWFSUrl.trim()) throw new Error("آدرس سرویس WFS الزامی است.");
        if (!dsmWFSTypeName.trim()) throw new Error("نام لایه (TypeName) الزامی است.");

        result = await registerWFSSource({
          project_id: activeProject?.project_id || getDsmUploadProjectId() || null,
          display_name: dsmWFSName.trim() || dsmWFSTypeName.trim(),
          base_url: dsmWFSUrl.trim(),
          type_name: dsmWFSTypeName.trim(),
          version: dsmWFSVersion || "2.0.0",
          max_features: dsmWFSMaxFeatures ? parseInt(dsmWFSMaxFeatures) : 1000,
        });
      } else if (dsmTab === "url") {
        if (!dsmURLValue.trim()) throw new Error("آدرس URL الزامی است.");

        result = await registerURLSource({
          project_id: activeProject?.project_id || getDsmUploadProjectId() || null,
          display_name: dsmURLName.trim() || undefined,
          url: dsmURLValue.trim(),
          kind: dsmURLKind || "vector",
        });
      } else if (dsmTab === "csv") {
        if (!dsmCSVUrl.trim() && !dsmCSVTableName.trim()) {
          throw new Error("برای CSV/Table حداقل URL یا نام جدول الزامی است.");
        }

        result = await registerCSVTableSource({
          project_id: activeProject?.project_id || getDsmUploadProjectId() || null,
          display_name: dsmCSVName.trim() || dsmCSVTableName.trim() || undefined,
          url: dsmCSVUrl.trim() || undefined,
          table_name: dsmCSVTableName.trim() || undefined,
          x_column: dsmCSVXColumn.trim() || undefined,
          y_column: dsmCSVYColumn.trim() || undefined,
          delimiter: dsmCSVDelimiter || ",",
          encoding: dsmCSVEncoding || "utf-8",
          crs: dsmCSVCrs || "EPSG:4326",
          has_header: Boolean(dsmCSVHasHeader),
        });
      } else if (dsmTab === "wms") {
        if (!dsmWMSUrl.trim()) throw new Error("آدرس سرویس WMS الزامی است.");
        if (!dsmWMSLayer.trim()) throw new Error("نام لایه WMS الزامی است.");

        result = await registerWMSSource({
          project_id: activeProject?.project_id || getDsmUploadProjectId() || null,
          display_name: dsmWMSName.trim() || dsmWMSLayer.trim(),
          base_url: dsmWMSUrl.trim(),
          layer_name: dsmWMSLayer.trim(),
          version: dsmWMSVersion || "1.3.0",
          format: dsmWMSFormat || "image/png",
          crs: dsmWMSCrs || "EPSG:3857",
          transparent: Boolean(dsmWMSTransparent),
          attribution: dsmWMSAttribution.trim() || undefined,
          opacity: dsmWMSOpacity ? Number(dsmWMSOpacity) : 0.85,
        });
      }

      if (result) {
        setUploads((prev) => {
          const list = Array.isArray(prev) ? prev : [];
          const resultId = result.upload_id || result.data_source_id || result.id;
          const filtered = list.filter((item) => {
            const itemId = item.upload_id || item.data_source_id || item.id;
            return itemId !== resultId;
          });
          return [result, ...filtered];
        });

        await refreshAfterDsmFileUpload(result);
      }

      setDsmModalOpen(false);
      resetDsmForms();

    } catch (err) {
      setDsmError(err?.message || "خطا در ثبت منبع داده.");
    } finally {
      setDsmBusy(false);
    }
  }

  async function handleConfirmDeleteModal() {
    if (!deleteModalUpload?.upload_id) return null;

    setModalError("");
    setModalBusy(true);

    try {
      await deleteDataSource(deleteModalUpload.upload_id);

      if (selectedUpload?.upload_id === deleteModalUpload.upload_id) {
        setSelectedUpload(null);
      }

      if (previewModalData?.upload_id === deleteModalUpload.upload_id) {
        setPreviewModalData(null);
      }

      if (activeProject?.project_id) {
        const full = await getProject(activeProject.project_id);
        setActiveProject(full);
        await refreshUploads(full);
      } else {
        await refreshUploads();
      }

      setDeleteModalUpload(null);
      return true;
    } catch (err) {
      setModalError(err.message);
      throw err;
    } finally {
      setModalBusy(false);
    }
  }

  function closeAllModals() {
    if (modalBusy) return;

    setPreviewModalData(null);
    setEditModalUpload(null);
    setDeleteModalUpload(null);
    setDsmModalOpen(false);
    setDsmError("");
    setModalError("");
  }

  return (
    <div className="workbench-shell">
      <WorkbenchSidebar
        activeTool={activeTool}
        onSelectTool={setActiveTool}
        health={health}
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
        onPreviewUpload={handlePreviewUpload}
        onEditUpload={handleEditUpload}
        onDeleteUpload={handleDeleteUpload}
        onAddExternalSource={openDsmModal}
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
          layerWorkspace={layerWorkspace}
          onLayerWorkspaceChange={setLayerWorkspace}
        />
      </main>

      <InspectorPanel
        response={response}
        mapLayers={mapLayers}
        outputManifest={outputManifest}
        activeRequest={activeRequest}
        loading={loading}
        error={globalError}
        layerWorkspace={layerWorkspace}
        onLayerWorkspaceChange={setLayerWorkspace}
      />

      <Modal
        open={Boolean(previewModalData)}
        title="Data Source Preview"
        subtitle={getDataSourceName(previewModalData)}
        onClose={closeAllModals}
        size="lg"
        footer={
          <button type="button" onClick={closeAllModals} disabled={modalBusy}>
            بستن
          </button>
        }
      >
        {modalError ? <div className="alert error">{modalError}</div> : null}

        <div className="modal-info-grid">
          <span>
            <small>Kind</small>
            <b>{getDataSourceKind(previewModalData)}</b>
          </span>
          <span>
            <small>Source</small>
            <b>{previewModalData?.source_type || "file"}</b>
          </span>
          <span>
            <small>Status</small>
            <b>{getDataSourceStatus(previewModalData)}</b>
          </span>
          <span>
            <small>Format</small>
            <b>{previewModalData?.extension || "—"}</b>
          </span>
          <span>
            <small>Size</small>
            <b>{formatPreviewBytes(getDataSourceSize(previewModalData))}</b>
          </span>
          <span>
            <small>Created</small>
            <b>{formatPreviewDateTime(previewModalData?.created_at)}</b>
          </span>
          <span>
            <small>Updated</small>
            <b>{formatPreviewDateTime(getDataSourceTime(previewModalData))}</b>
          </span>
          <span>
            <small>File</small>
            <b>{previewModalData?.filename || previewModalData?.original_filename || "—"}</b>
          </span>
        </div>

        {previewModalData?.description ? (
          <div className="modal-section">
            <h4>Description</h4>
            <p>{previewModalData.description}</p>
          </div>
        ) : null}

        {Array.isArray(previewModalData?.tags) && previewModalData.tags.length ? (
          <div className="modal-section">
            <h4>Tags</h4>
            <div className="chip-row">
              {previewModalData.tags.map((tag) => (
                <span key={tag} className="chip">
                  {tag}
                </span>
              ))}
            </div>
          </div>
        ) : null}

        <div className="modal-section">
          <h4>Dataset Summary</h4>
          <div className="modal-info-grid">
            <span>
              <small>Geometry</small>
              <b>{getDataSourceGeometryType(previewModalData) || "—"}</b>
            </span>
            <span>
              <small>Features</small>
              <b>{getPreviewFeatureCount(previewModalData) ?? "—"}</b>
            </span>
            <span>
              <small>CRS</small>
              <b>{getPreviewCrsLabel(previewModalData)}</b>
            </span>
            <span>
              <small>BBox</small>
              <b>{getPreviewBBox(previewModalData)}</b>
            </span>
            <span>
              <small>Raster Size</small>
              <b>{getPreviewRasterShape(previewModalData)}</b>
            </span>
            <span>
              <small>Bands</small>
              <b>{getPreviewBandCount(previewModalData) ?? "—"}</b>
            </span>
          </div>

          {getPreviewPropertyKeys(previewModalData).length ? (
            <div className="modal-subsection">
              <small>Property Keys</small>
              <div className="chip-row">
                {getPreviewPropertyKeys(previewModalData).slice(0, 20).map((key) => (
                  <span key={key} className="chip">
                    {key}
                  </span>
                ))}
              </div>
            </div>
          ) : (
            <p className="muted-note">
              {getDataSourceKind(previewModalData) === "raster"
                ? "Preview محتوایی برای فایل رستری هنوز محدود است، اما metadata فایل در دسترس است."
                : "اطلاعات summary بیشتری برای این منبع داده موجود نیست."}
            </p>
          )}
        </div>

        <DataSourcePreviewMap payload={previewModalData} />

        <details className="modal-section">
          <summary>Raw Preview JSON</summary>
          <div className="modal-json-block">
            <pre>{JSON.stringify(previewModalData?.preview || previewModalData || {}, null, 2)}</pre>
          </div>
        </details>
      </Modal>

      <Modal
        open={Boolean(editModalUpload)}
        title="Edit Data Source"
        subtitle={
          editModalUpload?.display_name ||
          editModalUpload?.name ||
          editModalUpload?.filename ||
          editModalUpload?.upload_id
        }
        onClose={closeAllModals}
        footer={
          <>
            <button type="button" onClick={closeAllModals} disabled={modalBusy}>
              انصراف
            </button>
            <button type="button" onClick={handleSaveEditModal} disabled={modalBusy}>
              {modalBusy ? "در حال ذخیره..." : "ذخیره تغییرات"}
            </button>
          </>
        }
      >
        <div className="modal-form">
<label>
            نام نمایشی
            <input
              value={editName}
              onChange={(event) => setEditName(event.target.value)}
              placeholder="Display name"
              disabled={modalBusy}
            />
          </label>

          <label>
            توضیح
            <textarea
              rows={3}
              value={editDescription}
              onChange={(event) => setEditDescription(event.target.value)}
              placeholder="اختیاری"
              disabled={modalBusy}
            />
          </label>

          <label>
            تگ‌ها
            <input
              value={editTags}
              onChange={(event) => setEditTags(event.target.value)}
              placeholder="sample, tehran, vector"
              disabled={modalBusy}
            />
            <small className="field-hint">
              تگ‌ها را با کاما جدا کنید — مثلاً: tehran, vector, 2024
            </small>
          </label>

          {modalError ? <div className="alert error">{modalError}</div> : null}
        </div>
      </Modal>

      <Modal
        open={Boolean(deleteModalUpload)}
        title="Delete Data Source"
        subtitle={
          deleteModalUpload?.display_name ||
          deleteModalUpload?.name ||
          deleteModalUpload?.filename ||
          deleteModalUpload?.upload_id
        }
        onClose={closeAllModals}
        footer={
          <>
            <button type="button" onClick={closeAllModals} disabled={modalBusy}>
              انصراف
            </button>
            <button
              type="button"
              className="danger"
              onClick={handleConfirmDeleteModal}
              disabled={modalBusy}
            >
              {modalBusy ? "در حال حذف..." : "حذف"}
            </button>
          </>
        }
      >
        <div className="modal-delete-copy">
          <p>آیا از حذف این منبع داده مطمئن هستید؟</p>
          <ul>
            <li>از storage حذف می‌شود</li>
            <li>از پروژه فعال جدا می‌شود</li>
            <li>این عملیات قابل بازگشت نیست</li>
          </ul>

          {modalError ? <div className="alert error">{modalError}</div> : null}
        </div>
      </Modal>

      <Modal
        open={dsmModalOpen}
        title="ثبت منبع داده خارجی (DSM)"
        subtitle="اتصال به دیتابیس، سرویس‌های مکانی یا API"
        onClose={() => !dsmBusy && setDsmModalOpen(false)}
        footer={
          <>
            <button type="button" onClick={() => setDsmModalOpen(false)} disabled={dsmBusy}>
              انصراف
            </button>
            <button
              type="button"
              className="primary"
              onClick={dsmTab === "file" ? handleDsmFileUpload : handleDsmSubmit}
              disabled={dsmBusy || (dsmTab === "file" && !dsmUploadFile)}
            >
              {dsmBusy
                ? (dsmTab === "file" ? "در حال آپلود..." : "در حال ثبت...")
                : (dsmTab === "file" ? "آپلود فایل" : "ثبت منبع داده")}
            </button>
          </>
        }
      >
        <div className="dsm-container dsm-container-pro">
          <div className="dsm-hero">
            <div>
              <span className="dsm-kicker">Data Source Manager</span>
              <h3>ثبت و اتصال منبع داده</h3>
              <p>
                منبع داده خارجی، سرویس مکانی، URL مستقیم یا فایل وکتور/رستر را به پروژه اضافه کنید.
              </p>
            </div>
            <div className="dsm-hero-badge">
              <span>DSM</span>
            </div>
          </div>

          <div className="dsm-tabs-header dsm-tabs-header-pro" role="tablist" aria-label="DSM source type">
            <button
              type="button"
              className={dsmTab === "postgis" ? "active" : ""}
              onClick={() => setDsmTab("postgis")}
            >
              <span>PostGIS</span>
              <small>Database</small>
            </button>
            <button
              type="button"
              className={dsmTab === "wfs" ? "active" : ""}
              onClick={() => setDsmTab("wfs")}
            >
              <span>WFS</span>
              <small>Service</small>
            </button>
            <button
              type="button"
              className={dsmTab === "url" ? "active" : ""}
              onClick={() => setDsmTab("url")}
            >
              <span>URL</span>
              <small>External</small>
            </button>
            <button
              type="button"
              className={dsmTab === "file" ? "active" : ""}
              onClick={() => setDsmTab("file")}
            >
              <span>File</span>
              <small>Raster / Vector</small>
            </button>
            <button
              type="button"
              className={dsmTab === "csv" ? "active" : ""}
              onClick={() => setDsmTab("csv")}
            >
              <span>CSV/Table</span>
              <small>Tabular</small>
            </button>
            <button
              type="button"
              className={dsmTab === "wms" ? "active" : ""}
              onClick={() => setDsmTab("wms")}
            >
              <span>WMS</span>
              <small>Map Service</small>
            </button>
          </div>

          <div className="dsm-form-body dsm-form-body-pro">
            {dsmTab === "postgis" && (
              <div className="dsm-grid dsm-grid-pro">
                <label className="dsm-field">
                  <span>نام نمایشی</span>
                  <input dir="ltr" value={dsmPGName} onChange={e => setDsmPGName(e.target.value)} placeholder="Main Database" />
                </label>

                <div className="dsm-row-2">
                  <label className="dsm-field">
                    <span>Host</span>
                    <input dir="ltr" value={dsmPGHost} onChange={e => setDsmPGHost(e.target.value)} placeholder="localhost" />
                  </label>
                  <label className="dsm-field">
                    <span>Port</span>
                    <input dir="ltr" value={dsmPGPort} onChange={e => setDsmPGPort(e.target.value)} placeholder="5432" />
                  </label>
                </div>

                <div className="dsm-row-2">
                  <label className="dsm-field">
                    <span>Database</span>
                    <input dir="ltr" value={dsmPGDatabase} onChange={e => setDsmPGDatabase(e.target.value)} placeholder="spatial_db" />
                  </label>
                  <label className="dsm-field">
                    <span>Schema</span>
                    <input dir="ltr" value={dsmPGSchema} onChange={e => setDsmPGSchema(e.target.value)} placeholder="public" />
                  </label>
                </div>

                <div className="dsm-row-2">
                  <label className="dsm-field">
                    <span>Username</span>
                    <input dir="ltr" value={dsmPGUser} onChange={e => setDsmPGUser(e.target.value)} placeholder="postgres" />
                  </label>
                  <label className="dsm-field">
                    <span>Password</span>
                    <input dir="ltr" type="password" value={dsmPGPassword} onChange={e => setDsmPGPassword(e.target.value)} placeholder="••••••••" />
                  </label>
                </div>

                <label className="dsm-field">
                  <span>Table Name <b>Required</b></span>
                  <input dir="ltr" value={dsmPGTable} onChange={e => setDsmPGTable(e.target.value)} placeholder="my_spatial_table" />
                </label>

                <label className="dsm-field">
                  <span>Where Clause <em>Optional</em></span>
                  <input dir="ltr" value={dsmPGWhere} onChange={e => setDsmPGWhere(e.target.value)} placeholder="status = 'active'" />
                </label>
              </div>
            )}

            {dsmTab === "wfs" && (
              <div className="dsm-grid dsm-grid-pro">
                <label className="dsm-field">
                  <span>نام نمایشی</span>
                  <input dir="ltr" value={dsmWFSName} onChange={e => setDsmWFSName(e.target.value)} placeholder="Municipality WFS" />
                </label>

                <label className="dsm-field">
                  <span>WFS Server URL</span>
                  <input dir="ltr" value={dsmWFSUrl} onChange={e => setDsmWFSUrl(e.target.value)} placeholder="https://geoserver.example.com/wfs" />
                </label>

                <label className="dsm-field">
                  <span>Layer TypeName</span>
                  <input dir="ltr" value={dsmWFSTypeName} onChange={e => setDsmWFSTypeName(e.target.value)} placeholder="workspace:layer_name" />
                </label>

                <div className="dsm-row-2">
                  <label className="dsm-field">
                    <span>Version</span>
                    <input dir="ltr" value={dsmWFSVersion} onChange={e => setDsmWFSVersion(e.target.value)} placeholder="2.0.0" />
                  </label>
                  <label className="dsm-field">
                    <span>Max Features</span>
                    <input dir="ltr" type="number" value={dsmWFSMaxFeatures} onChange={e => setDsmWFSMaxFeatures(e.target.value)} placeholder="1000" />
                  </label>
                </div>
              </div>
            )}

            {dsmTab === "url" && (
              <div className="dsm-grid dsm-grid-pro">
                <label className="dsm-field">
                  <span>نام نمایشی</span>
                  <input dir="ltr" value={dsmURLName} onChange={e => setDsmURLName(e.target.value)} placeholder="Tehran Points URL" />
                </label>

                <label className="dsm-field">
                  <span>Direct URL</span>
                  <input dir="ltr" value={dsmURLValue} onChange={e => setDsmURLValue(e.target.value)} placeholder="https://data.example.com/points.geojson" />
                </label>

                <label className="dsm-field">
                  <span>Data Type</span>
                  <select dir="ltr" value={dsmURLKind} onChange={e => setDsmURLKind(e.target.value)}>
                    <option value="vector">Vector (GeoJSON)</option>
                    <option value="raster">Raster (GeoTIFF)</option>
                  </select>
                </label>
              </div>
            )}


            {dsmTab === "csv" && (
              <div className="dsm-grid dsm-grid-pro">
                <label className="dsm-field">
                  <span>نام نمایشی</span>
                  <input dir="ltr" value={dsmCSVName} onChange={e => setDsmCSVName(e.target.value)} placeholder="Tehran CSV Points" />
                </label>

                <label className="dsm-field">
                  <span>CSV URL <em>Optional</em></span>
                  <input dir="ltr" value={dsmCSVUrl} onChange={e => setDsmCSVUrl(e.target.value)} placeholder="https://data.example.com/points.csv" />
                </label>

                <label className="dsm-field">
                  <span>Table Name <em>Optional</em></span>
                  <input dir="ltr" value={dsmCSVTableName} onChange={e => setDsmCSVTableName(e.target.value)} placeholder="survey_points_table" />
                </label>

                <div className="dsm-row-2">
                  <label className="dsm-field">
                    <span>X / Longitude Column</span>
                    <input dir="ltr" value={dsmCSVXColumn} onChange={e => setDsmCSVXColumn(e.target.value)} placeholder="longitude" />
                  </label>
                  <label className="dsm-field">
                    <span>Y / Latitude Column</span>
                    <input dir="ltr" value={dsmCSVYColumn} onChange={e => setDsmCSVYColumn(e.target.value)} placeholder="latitude" />
                  </label>
                </div>

                <div className="dsm-row-2">
                  <label className="dsm-field">
                    <span>CRS</span>
                    <input dir="ltr" value={dsmCSVCrs} onChange={e => setDsmCSVCrs(e.target.value)} placeholder="EPSG:4326" />
                  </label>
                  <label className="dsm-field">
                    <span>Delimiter</span>
                    <input dir="ltr" value={dsmCSVDelimiter} onChange={e => setDsmCSVDelimiter(e.target.value)} placeholder="," />
                  </label>
                </div>

                <div className="dsm-row-2">
                  <label className="dsm-field">
                    <span>Encoding</span>
                    <input dir="ltr" value={dsmCSVEncoding} onChange={e => setDsmCSVEncoding(e.target.value)} placeholder="utf-8" />
                  </label>
                  <label className="dsm-field dsm-check-field">
                    <span>Header</span>
                    <label className="dsm-inline-check">
                      <input type="checkbox" checked={dsmCSVHasHeader} onChange={e => setDsmCSVHasHeader(e.target.checked)} />
                      <b>First row contains column names</b>
                    </label>
                  </label>
                </div>
              </div>
            )}

            {dsmTab === "wms" && (
              <div className="dsm-grid dsm-grid-pro">
                <label className="dsm-field">
                  <span>نام نمایشی</span>
                  <input dir="ltr" value={dsmWMSName} onChange={e => setDsmWMSName(e.target.value)} placeholder="City Base Map WMS" />
                </label>

                <label className="dsm-field">
                  <span>WMS Server URL <b>Required</b></span>
                  <input dir="ltr" value={dsmWMSUrl} onChange={e => setDsmWMSUrl(e.target.value)} placeholder="https://geoserver.example.com/geoserver/wms" />
                </label>

                <label className="dsm-field">
                  <span>Layer Name <b>Required</b></span>
                  <input dir="ltr" value={dsmWMSLayer} onChange={e => setDsmWMSLayer(e.target.value)} placeholder="workspace:layer_name" />
                </label>

                <div className="dsm-row-2">
                  <label className="dsm-field">
                    <span>Version</span>
                    <input dir="ltr" value={dsmWMSVersion} onChange={e => setDsmWMSVersion(e.target.value)} placeholder="1.3.0" />
                  </label>
                  <label className="dsm-field">
                    <span>Format</span>
                    <select dir="ltr" value={dsmWMSFormat} onChange={e => setDsmWMSFormat(e.target.value)}>
                      <option value="image/png">image/png</option>
                      <option value="image/jpeg">image/jpeg</option>
                      <option value="image/geotiff">image/geotiff</option>
                    </select>
                  </label>
                </div>

                <div className="dsm-row-2">
                  <label className="dsm-field">
                    <span>CRS</span>
                    <input dir="ltr" value={dsmWMSCrs} onChange={e => setDsmWMSCrs(e.target.value)} placeholder="EPSG:3857" />
                  </label>
                  <label className="dsm-field">
                    <span>Opacity</span>
                    <input dir="ltr" type="number" step="0.05" min="0" max="1" value={dsmWMSOpacity} onChange={e => setDsmWMSOpacity(e.target.value)} placeholder="0.85" />
                  </label>
                </div>

                <label className="dsm-field">
                  <span>Attribution <em>Optional</em></span>
                  <input dir="ltr" value={dsmWMSAttribution} onChange={e => setDsmWMSAttribution(e.target.value)} placeholder="© GeoServer / Municipality" />
                </label>

                <label className="dsm-field dsm-check-field">
                  <span>Transparency</span>
                  <label className="dsm-inline-check">
                    <input type="checkbox" checked={dsmWMSTransparent} onChange={e => setDsmWMSTransparent(e.target.checked)} />
                    <b>Request transparent tiles</b>
                  </label>
                </label>
              </div>
            )}

            {dsmTab === "file" && (
              <div className="dsm-upload-panel">
                <div className="dsm-upload-head">
                  <div>
                    <strong>آپلود فایل مکانی</strong>
                    <p>فایل وکتور یا رستر را انتخاب کنید تا به منابع داده پروژه اضافه شود.</p>
                  </div>
                </div>

                <div className="dsm-upload-switch">
                  <button
                    type="button"
                    className={dsmUploadKind === "vector" ? "active" : ""}
                    onClick={() => {
                      setDsmUploadKind("vector");
                      setDsmUploadFile(null);
                    }}
                  >
                    Vector
                    <small>GeoJSON / SHP / GPKG</small>
                  </button>
                  <button
                    type="button"
                    className={dsmUploadKind === "raster" ? "active" : ""}
                    onClick={() => {
                      setDsmUploadKind("raster");
                      setDsmUploadFile(null);
                    }}
                  >
                    Raster
                    <small>GeoTIFF / TIFF</small>
                  </button>
                </div>

                <label className="dsm-file-drop">
                  <input
                    type="file"
                    accept={dsmUploadKind === "raster" ? ".tif,.tiff,.geotiff" : ".geojson,.json,.zip,.shp,.gpkg"}
                    onChange={e => setDsmUploadFile(e.target.files?.[0] || null)}
                  />
                  <span className="dsm-file-icon">⬆</span>
                  <strong>{dsmUploadFile ? dsmUploadFile.name : "Choose file"}</strong>
                  <small>
                    {dsmUploadKind === "raster"
                      ? "Supported raster: .tif, .tiff, .geotiff"
                      : "Supported vector: .geojson, .json, .zip, .shp, .gpkg"}
                  </small>
                </label>
              </div>
            )}

            {dsmError && <div className="alert error dsm-error">{dsmError}</div>}
          </div>
        </div>
      </Modal>
    </div>
  );
}
