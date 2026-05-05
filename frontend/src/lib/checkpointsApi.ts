type Fetcher = (url: string, options?: RequestInit) => Promise<Response>;

export interface JobCheckpoint {
  step: number;
  source: "local" | "hub";
  ref: string;
}

export interface PolicyConfigSummary {
  policy_type: string | null;
  image_features: Record<string, { height: number; width: number }>;
  requires_task: boolean;
}

export async function listJobCheckpoints(
  baseUrl: string,
  fetcher: Fetcher,
  jobId: string,
): Promise<JobCheckpoint[]> {
  const r = await fetcher(`${baseUrl}/jobs/${jobId}/checkpoints`);
  if (!r.ok) {
    throw new Error(`List checkpoints failed: ${r.status}`);
  }
  const body = await r.json();
  return body.checkpoints;
}

export async function getCheckpointPolicyConfig(
  baseUrl: string,
  fetcher: Fetcher,
  jobId: string,
  step: number,
): Promise<PolicyConfigSummary> {
  const r = await fetcher(
    `${baseUrl}/jobs/${jobId}/checkpoints/${step}/policy-config`,
  );
  if (!r.ok) {
    throw new Error(`Load policy config failed: ${r.status}`);
  }
  return r.json();
}
