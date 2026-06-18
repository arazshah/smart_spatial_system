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
  deleteDataSource
} from "./api/client";
import InspectorPanel from "./components/InspectorPanel";
import MapStage from "./components/MapStage";
import TopQueryBar from "./components/TopQueryBar";
import WorkbenchDrawer from "./components/WorkbenchDrawer";
import WorkbenchSidebar from "./components/WorkbenchSidebar";
import Modal from "./components/Modal";
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

  const [activeTool, setActiveTool] = useState(null);
  const [loading, setLoading] = useState(false);
  const [bootLoading, setBootLoading] = useState(true);
  const [globalError, setGlobalError] = useState("");

  const [previewModalData, setPreviewModalData] = useState(null);
  const [editModalUpload, setEditModalUpload] = useState(null);
  const [deleteModalUpload, setDeleteModalUpload] = useState(null);

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
      setUploads(asList(payload, ["uploads"]));
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

    setModalError("");
    setModalBusy(true);

    try {
      const payload = await previewDataSource(upload.upload_id);
      setPreviewModalData(payload);
      return payload;
    } catch (err) {
      setModalError(err.message);
      throw err;
    } finally {
      setModalBusy(false);
    }
  }

  async function handleEditUpload(upload) {
    if (!upload?.upload_id) return null;

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

    setModalError("");
    setDeleteModalUpload(upload);
    return upload;
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
        outputManifest={outputManifest}
        activeRequest={activeRequest}
        loading={loading}
        error={globalError}
      />

      <Modal
        open={Boolean(previewModalData)}
        title="Data Source Preview"
        subtitle={
          previewModalData?.display_name ||
          previewModalData?.name ||
          previewModalData?.filename ||
          previewModalData?.upload_id
        }
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
            <b>{previewModalData?.kind || "data"}</b>
          </span>
          <span>
            <small>Format</small>
            <b>{previewModalData?.extension || "—"}</b>
          </span>
          <span>
            <small>Size</small>
            <b>{previewModalData?.size_bytes || "—"}</b>
          </span>
        </div>

        <div className="modal-json-block">
          <pre>{JSON.stringify(previewModalData?.preview || {}, null, 2)}</pre>
        </div>
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
              rows={4}
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
    </div>
  );
}
