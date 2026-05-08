const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

export const warmupDemoDatabase = async (): Promise<void> => {
  try {
    await fetch(`${API_BASE}/api/demo/warmup_database`, {
      method: 'POST',
    });
  } catch (error) {
    console.warn('Demo database warmup request failed:', error);
  }
};
