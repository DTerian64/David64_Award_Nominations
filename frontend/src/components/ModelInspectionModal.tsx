import React, { useEffect, useState } from 'react';
import {
  AlertCircle, ArrowRight, BarChart3, BrainCircuit, Database,
  FileBox, RefreshCw, Trees, X,
} from 'lucide-react';
import { getAccessToken } from '../services/api';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

export type InspectableModel = 'rf' | 'gnn';

interface ArtifactInfo {
  file_name: string;
  role: string;
  size_bytes: number;
  sha256: string;
}

interface FeatureImportance {
  name: string;
  importance: number;
}

interface ForestSummary {
  available: boolean;
  estimator?: string;
  classes?: string[];
  feature_count: number;
  tree_count?: number;
  hyperparameters?: Record<string, unknown>;
  tree_statistics?: Record<string, number>;
  features: FeatureImportance[];
}

interface TensorInfo {
  name: string;
  shape: number[];
  dtype: string;
  parameter_count: number;
}

interface RelationInfo {
  source: string;
  relationship: string;
  target: string;
}

interface ArchitecturePart {
  type: string;
  role: string;
  layer_count?: number;
  embedding_dimension?: number;
  aggregation?: string;
  input_dimension?: number;
  layers?: number[];
  dropout?: number;
  parameter_count: number;
  tensor_count: number;
  tensors: TensorInfo[];
  relations?: RelationInfo[];
}

interface ModelManifest {
  schema_version: number;
  artifact_type: string;
  tenant_id: number;
  model_version: string;
  generated_at: string;
  description: string;
  artifacts: ArtifactInfo[];
  training?: Record<string, unknown>;
  data_profile?: Record<string, unknown>;
  models?: Record<string, ForestSummary>;
  architecture?: {
    encoder: ArchitecturePart;
    decoder: ArchitecturePart;
  };
  features?: {
    user: string[];
    nomination: string[];
  };
}

interface ManifestResponse {
  available: boolean;
  component: InspectableModel;
  message?: string;
  manifest?: ModelManifest;
}

interface Props {
  component: InspectableModel;
  impersonatedUPN?: string;
  onClose: () => void;
}

const prettyLabel = (value: string) =>
  value.replace(/_/g, ' ').replace(/\b\w/g, character => character.toUpperCase());

const forestModelLabel = (name: string) =>
  name.toLowerCase() === 'p2p' ? 'Peer to Peer' : prettyLabel(name);

const displayValue = (value: unknown): string => {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (typeof value === 'number') {
    if (Math.abs(value) > 0 && Math.abs(value) < 0.01) return value.toPrecision(3);
    return value.toLocaleString(undefined, { maximumFractionDigits: 4 });
  }
  if (Array.isArray(value)) return value.join(', ');
  return String(value);
};

const formatBytes = (bytes: number) => {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

const MetricsGrid: React.FC<{ values?: Record<string, unknown>; title: string }> = ({ values, title }) => {
  const entries = Object.entries(values || {});
  if (entries.length === 0) return null;
  return (
    <section>
      <h4 className="mb-2 text-sm font-semibold text-gray-700">{title}</h4>
      <dl className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-4">
        {entries.map(([key, value]) => (
          <div key={key} className="rounded-md bg-gray-50 px-3 py-2 text-xs">
            <dt className="text-gray-400">{prettyLabel(key)}</dt>
            <dd className="mt-0.5 break-words font-medium text-gray-700">{displayValue(value)}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
};

const Artifacts: React.FC<{ artifacts: ArtifactInfo[] }> = ({ artifacts }) => (
  <section>
    <h4 className="mb-2 text-sm font-semibold text-gray-700">Published artifacts</h4>
    <div className="grid gap-2 md:grid-cols-2">
      {artifacts.map(artifact => (
        <div key={artifact.file_name} className="flex items-start gap-3 rounded-lg border border-gray-200 p-3">
          <FileBox className="mt-0.5 h-5 w-5 shrink-0 text-indigo-500" />
          <div className="min-w-0 text-xs">
            <div className="break-all font-mono font-medium text-gray-700">{artifact.file_name}</div>
            <div className="mt-1 text-gray-500">{prettyLabel(artifact.role)} · {formatBytes(artifact.size_bytes)}</div>
            <div className="mt-1 truncate font-mono text-[10px] text-gray-400" title={artifact.sha256}>SHA-256 {artifact.sha256}</div>
          </div>
        </div>
      ))}
    </div>
  </section>
);

const ForestModel: React.FC<{ name: string; summary: ForestSummary }> = ({ name, summary }) => {
  if (!summary.available) {
    return (
      <section className="rounded-lg border border-dashed border-gray-200 p-4">
        <h4 className="font-semibold text-gray-700">{forestModelLabel(name)} model</h4>
        <p className="mt-1 text-sm text-gray-500">Not produced because the training data did not support this model.</p>
      </section>
    );
  }
  const maximumImportance = Math.max(...summary.features.map(feature => feature.importance), 0.0001);
  return (
    <section className="rounded-lg border border-gray-200 p-4">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-2">
        <div>
          <h4 className="flex items-center gap-2 font-semibold text-gray-800"><Trees className="h-4 w-4 text-emerald-600" />{forestModelLabel(name)} model</h4>
          <p className="mt-1 text-xs text-gray-500">{summary.estimator} · {summary.tree_count} trees · {summary.feature_count} features</p>
        </div>
        <span className="rounded-full border border-green-200 bg-green-50 px-2 py-0.5 text-xs text-green-700">Available</span>
      </div>

      <MetricsGrid values={summary.hyperparameters} title="Hyperparameters" />
      <div className="mt-4"><MetricsGrid values={summary.tree_statistics} title="Tree structure" /></div>

      <div className="mt-4">
        <h5 className="mb-2 text-sm font-semibold text-gray-700">Feature importance</h5>
        <div className="max-h-72 space-y-2 overflow-y-auto pr-2">
          {summary.features.map(feature => (
            <div key={feature.name} className="grid grid-cols-[minmax(9rem,1fr)_2fr_4rem] items-center gap-2 text-xs">
              <span className="truncate text-gray-600" title={feature.name}>{feature.name}</span>
              <div className="h-2 overflow-hidden rounded-full bg-gray-100">
                <div className="h-full rounded-full bg-emerald-500" style={{ width: `${Math.max(1, feature.importance / maximumImportance * 100)}%` }} />
              </div>
              <span className="text-right font-mono text-gray-500">{feature.importance.toFixed(4)}</span>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
};

const RandomForestView: React.FC<{ manifest: ModelManifest; imageUrl: string | null }> = ({ manifest, imageUrl }) => (
  <div className="space-y-5">
    {imageUrl && (
      <section className="overflow-hidden rounded-lg border border-gray-200 bg-white p-3">
        <h4 className="mb-2 flex items-center gap-2 text-sm font-semibold text-gray-700"><BarChart3 className="h-4 w-4 text-indigo-500" />Fraud score distribution</h4>
        <img src={imageUrl} alt="Tenant Random Forest fraud-score distribution" className="w-full rounded" />
      </section>
    )}
    <div className="grid gap-4">
      {Object.entries(manifest.models || {}).map(([name, summary]) => (
        <ForestModel key={name} name={name} summary={summary} />
      ))}
    </div>
    <MetricsGrid values={manifest.training} title="Training results" />
    <MetricsGrid values={manifest.data_profile} title="Training data profile" />
  </div>
);

const TensorTable: React.FC<{ part: ArchitecturePart }> = ({ part }) => (
  <details className="mt-3 rounded-md bg-gray-50 p-3 text-xs">
    <summary className="cursor-pointer font-medium text-indigo-600">View {part.tensor_count} parameter tensors</summary>
    <div className="mt-3 max-h-72 overflow-auto">
      <table className="w-full text-left">
        <thead className="sticky top-0 bg-gray-50 text-gray-400"><tr><th className="pb-1 pr-3">Tensor</th><th className="pb-1 pr-3">Shape</th><th className="pb-1 pr-3">Type</th><th className="pb-1 text-right">Parameters</th></tr></thead>
        <tbody className="divide-y divide-gray-200">
          {part.tensors.map(tensor => (
            <tr key={tensor.name}><td className="py-1.5 pr-3 font-mono text-gray-600">{tensor.name}</td><td className="py-1.5 pr-3 font-mono text-gray-600">[{tensor.shape.join(' × ')}]</td><td className="py-1.5 pr-3 text-gray-500">{tensor.dtype}</td><td className="py-1.5 text-right text-gray-600">{tensor.parameter_count.toLocaleString()}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  </details>
);

const ArchitectureCard: React.FC<{ title: string; part: ArchitecturePart }> = ({ title, part }) => (
  <section className="rounded-lg border border-gray-200 p-4">
    <h4 className="font-semibold text-gray-800">{title}</h4>
    <p className="mt-1 text-xs text-gray-500">{part.type} · {prettyLabel(part.role)}</p>
    <dl className="mt-3 grid grid-cols-2 gap-2 text-xs">
      <div className="rounded bg-gray-50 p-2"><dt className="text-gray-400">Parameters</dt><dd className="font-medium text-gray-700">{part.parameter_count.toLocaleString()}</dd></div>
      <div className="rounded bg-gray-50 p-2"><dt className="text-gray-400">Tensors</dt><dd className="font-medium text-gray-700">{part.tensor_count}</dd></div>
      {part.layer_count !== undefined && <div className="rounded bg-gray-50 p-2"><dt className="text-gray-400">Layers</dt><dd className="font-medium text-gray-700">{part.layer_count}</dd></div>}
      {part.embedding_dimension !== undefined && <div className="rounded bg-gray-50 p-2"><dt className="text-gray-400">Embedding</dt><dd className="font-medium text-gray-700">{part.embedding_dimension} dimensions</dd></div>}
      {part.input_dimension !== undefined && <div className="rounded bg-gray-50 p-2"><dt className="text-gray-400">Input</dt><dd className="font-medium text-gray-700">{part.input_dimension} values</dd></div>}
      {part.layers && <div className="rounded bg-gray-50 p-2"><dt className="text-gray-400">Shape</dt><dd className="font-medium text-gray-700">{part.layers.join(' → ')}</dd></div>}
    </dl>
    <TensorTable part={part} />
  </section>
);

const FeatureList: React.FC<{ title: string; features: string[] }> = ({ title, features }) => (
  <section>
    <h4 className="mb-2 text-sm font-semibold text-gray-700">{title}</h4>
    <div className="flex flex-wrap gap-1.5">
      {features.map(feature => <span key={feature} className="rounded bg-gray-100 px-2 py-1 text-xs text-gray-600">{feature}</span>)}
    </div>
  </section>
);

const GnnView: React.FC<{ manifest: ModelManifest }> = ({ manifest }) => {
  const architecture = manifest.architecture;
  if (!architecture) return null;
  return (
    <div className="space-y-5">
      <section className="rounded-lg border border-indigo-100 bg-indigo-50/40 p-4">
        <h4 className="mb-4 flex items-center gap-2 text-sm font-semibold text-gray-700"><BrainCircuit className="h-4 w-4 text-indigo-600" />Serving architecture</h4>
        <div className="flex flex-col items-stretch justify-center gap-2 text-center text-sm md:flex-row md:items-center">
          <div className="rounded-lg border border-indigo-200 bg-white px-4 py-3"><strong>GraphSAGE encoder</strong><div className="text-xs text-gray-500">Audit and retraining</div></div>
          <ArrowRight className="mx-auto h-5 w-5 rotate-90 text-indigo-300 md:rotate-0" />
          <div className="rounded-lg border border-blue-200 bg-white px-4 py-3"><Database className="mx-auto mb-1 h-4 w-4 text-blue-500" /><strong>User embeddings</strong><div className="text-xs text-gray-500">Stored in SQL</div></div>
          <ArrowRight className="mx-auto h-5 w-5 rotate-90 text-indigo-300 md:rotate-0" />
          <div className="rounded-lg border border-emerald-200 bg-white px-4 py-3"><strong>Serving decoder</strong><div className="text-xs text-gray-500">Live inference head</div></div>
          <ArrowRight className="mx-auto h-5 w-5 rotate-90 text-indigo-300 md:rotate-0" />
          <div className="rounded-lg border border-amber-200 bg-white px-4 py-3"><strong>Fraud probability</strong></div>
        </div>
      </section>

      <div className="grid gap-4 xl:grid-cols-2">
        <ArchitectureCard title="Encoder" part={architecture.encoder} />
        <ArchitectureCard title="Decoder head" part={architecture.decoder} />
      </div>

      {architecture.encoder.relations && (
        <section>
          <h4 className="mb-2 text-sm font-semibold text-gray-700">Graph relationships</h4>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {architecture.encoder.relations.map(relation => (
              <div key={`${relation.source}-${relation.relationship}-${relation.target}`} className="rounded-md bg-gray-50 px-3 py-2 text-center text-xs text-gray-600">
                {relation.source} <span className="font-medium text-indigo-600">—{relation.relationship}→</span> {relation.target}
              </div>
            ))}
          </div>
        </section>
      )}

      {manifest.features && <FeatureList title="User features" features={manifest.features.user} />}
      {manifest.features && <FeatureList title="Nomination features" features={manifest.features.nomination} />}
      <MetricsGrid values={manifest.training} title="Training and evaluation metrics" />
    </div>
  );
};

export const ModelInspectionModal: React.FC<Props> = ({ component, impersonatedUPN, onClose }) => {
  const [response, setResponse] = useState<ManifestResponse | null>(null);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let generatedImageUrl: string | null = null;

    const load = async () => {
      setLoading(true);
      setError(null);
      try {
        const token = await getAccessToken();
        const headers: Record<string, string> = { Authorization: `Bearer ${token}` };
        if (impersonatedUPN) headers['X-Impersonate-User'] = impersonatedUPN;
        const manifestResponse = await fetch(`${API_BASE_URL}/api/model-analysis/setup/models/${component}`, { headers });
        if (!manifestResponse.ok) {
          const body = await manifestResponse.json().catch(() => null) as { detail?: string } | null;
          throw new Error(body?.detail || `HTTP ${manifestResponse.status}`);
        }
        const body = await manifestResponse.json() as ManifestResponse;
        if (!cancelled) setResponse(body);

        if (component === 'rf') {
          const imageResponse = await fetch(`${API_BASE_URL}/api/model-analysis/setup/models/rf/visualization`, { headers });
          if (imageResponse.ok) {
            const image = await imageResponse.blob();
            if (!cancelled) {
              generatedImageUrl = URL.createObjectURL(image);
              setImageUrl(generatedImageUrl);
            }
          }
        }
      } catch (caught) {
        if (!cancelled) setError(caught instanceof Error ? caught.message : 'Failed to load model representation');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
      if (generatedImageUrl) URL.revokeObjectURL(generatedImageUrl);
    };
  }, [component, impersonatedUPN]);

  const manifest = response?.manifest;
  const title = component === 'rf' ? 'Random Forest model' : 'Graph Neural Network model';

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto bg-black/40 p-3 sm:p-6" role="dialog" aria-modal="true" aria-label={title}>
      <div className="mx-auto max-w-7xl overflow-hidden rounded-xl bg-white shadow-2xl">
        <header className="sticky top-0 z-10 flex items-start justify-between gap-4 border-b border-gray-200 bg-white px-5 py-4">
          <div>
            <h3 className="text-lg font-semibold text-gray-900">{title}</h3>
            <p className="mt-0.5 text-xs text-gray-500">Read-only representation of the tenant’s deployed model artifacts</p>
          </div>
          <button onClick={onClose} className="rounded p-2 text-gray-500 hover:bg-gray-100" title="Close model inspection" aria-label="Close model inspection"><X className="h-5 w-5" /></button>
        </header>

        <div className="space-y-5 p-4 sm:p-6">
          {loading && <div className="flex items-center justify-center gap-2 py-20 text-sm text-gray-400"><RefreshCw className="h-4 w-4 animate-spin" />Loading model representation…</div>}
          {!loading && error && <div className="flex items-start gap-2 rounded-lg bg-red-50 p-4 text-sm text-red-700"><AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />{error}</div>}
          {!loading && !error && response && !response.available && (
            <div className="space-y-4">
              <div className="rounded-lg border border-dashed border-gray-200 px-6 py-12 text-center"><FileBox className="mx-auto mb-3 h-10 w-10 text-gray-300" /><p className="font-medium text-gray-700">Representation not published yet</p><p className="mt-1 text-sm text-gray-500">{response.message}</p></div>
              {component === 'rf' && imageUrl && (
                <section className="overflow-hidden rounded-lg border border-gray-200 bg-white p-3">
                  <h4 className="mb-2 flex items-center gap-2 text-sm font-semibold text-gray-700"><BarChart3 className="h-4 w-4 text-indigo-500" />Available fraud score distribution</h4>
                  <img src={imageUrl} alt="Tenant Random Forest fraud-score distribution" className="w-full rounded" />
                </section>
              )}
            </div>
          )}
          {!loading && !error && manifest && (
            <>
              <section className="rounded-lg border border-gray-200 bg-gray-50 p-4">
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                  <div><div className="text-xs text-gray-400">Model version</div><div className="mt-1 break-all font-mono text-sm font-medium text-gray-700">{manifest.model_version}</div></div>
                  <div><div className="text-xs text-gray-400">Generated</div><div className="mt-1 text-sm font-medium text-gray-700">{new Date(manifest.generated_at).toLocaleString()}</div></div>
                  <div><div className="text-xs text-gray-400">Tenant</div><div className="mt-1 text-sm font-medium text-gray-700">{manifest.tenant_id}</div></div>
                  <div><div className="text-xs text-gray-400">Manifest schema</div><div className="mt-1 text-sm font-medium text-gray-700">Version {manifest.schema_version}</div></div>
                </div>
                <p className="mt-3 text-sm text-gray-600">{manifest.description}</p>
              </section>

              {component === 'rf' ? <RandomForestView manifest={manifest} imageUrl={imageUrl} /> : <GnnView manifest={manifest} />}
              <Artifacts artifacts={manifest.artifacts} />
            </>
          )}
        </div>
      </div>
    </div>
  );
};
