"""
Checkpointing Module
=====================

Saves intermediate pipeline results to allow resuming from failures.

Features:
- Save/load checkpoints at each pipeline stage
- Automatic timestamping
- Skip completed stages on resume
- Clean up old checkpoints
"""

import json
import pickle
from pathlib import Path
from datetime import datetime
from typing import Any, Optional
import pandas as pd

from config import config


class CheckpointManager:
    """Manage pipeline checkpoints for fault tolerance."""

    def __init__(self, run_id: str = None):
        """
        Initialize checkpoint manager.

        Args:
            run_id: Unique identifier for this pipeline run (default: timestamp)
        """
        self.run_id = run_id or datetime.now().strftime('%Y%m%d_%H%M%S')
        self.checkpoint_dir = config.CHECKPOINT_DIR / self.run_id
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

        self.metadata_file = self.checkpoint_dir / "metadata.json"
        self.metadata = self._load_metadata()

    def _load_metadata(self) -> dict:
        """Load checkpoint metadata."""
        if self.metadata_file.exists():
            try:
                with open(self.metadata_file, 'r') as f:
                    return json.load(f)
            except:
                return {}
        return {}

    def _save_metadata(self):
        """Save checkpoint metadata."""
        with open(self.metadata_file, 'w') as f:
            json.dump(self.metadata, f, indent=2)

    def save_checkpoint(self, stage: str, data: Any, description: str = ""):
        """
        Save a checkpoint for a pipeline stage.

        Args:
            stage: Name of the pipeline stage (e.g., 'reference_data', 'serp_results')
            data: Data to save (DataFrame, dict, list, etc.)
            description: Optional description of this checkpoint
        """
        checkpoint_file = self.checkpoint_dir / f"{stage}.pkl"

        try:
            # Save data based on type
            if isinstance(data, pd.DataFrame):
                # For DataFrames, save as both pickle and CSV for easy inspection
                data.to_pickle(checkpoint_file)
                data.to_csv(checkpoint_file.with_suffix('.csv'), index=False)
            else:
                # For other types, use pickle
                with open(checkpoint_file, 'wb') as f:
                    pickle.dump(data, f)

            # Update metadata
            self.metadata[stage] = {
                'timestamp': datetime.now().isoformat(),
                'description': description,
                'file': str(checkpoint_file),
                'type': type(data).__name__
            }
            self._save_metadata()

            print(f"💾 Checkpoint saved: {stage}")

        except Exception as e:
            print(f"⚠️  Failed to save checkpoint for {stage}: {e}")

    def load_checkpoint(self, stage: str) -> Optional[Any]:
        """
        Load a checkpoint for a pipeline stage.

        Args:
            stage: Name of the pipeline stage

        Returns:
            Loaded data, or None if checkpoint doesn't exist
        """
        if stage not in self.metadata:
            return None

        checkpoint_file = Path(self.metadata[stage]['file'])

        if not checkpoint_file.exists():
            return None

        try:
            # Try to load based on saved type
            if self.metadata[stage]['type'] == 'DataFrame':
                return pd.read_pickle(checkpoint_file)
            else:
                with open(checkpoint_file, 'rb') as f:
                    return pickle.load(f)

        except Exception as e:
            print(f"⚠️  Failed to load checkpoint for {stage}: {e}")
            return None

    def has_checkpoint(self, stage: str) -> bool:
        """Check if a checkpoint exists for a stage."""
        return stage in self.metadata

    def get_checkpoint_info(self, stage: str) -> Optional[dict]:
        """Get information about a checkpoint."""
        return self.metadata.get(stage)

    def list_checkpoints(self) -> dict:
        """List all available checkpoints."""
        return self.metadata

    def clear_checkpoints(self):
        """Delete all checkpoints for this run."""
        import shutil
        if self.checkpoint_dir.exists():
            shutil.rmtree(self.checkpoint_dir)
            print(f"🗑️  Cleared checkpoints for run {self.run_id}")


def find_latest_run() -> Optional[str]:
    """Find the most recent checkpoint run."""
    checkpoint_base = config.CHECKPOINT_DIR

    if not checkpoint_base.exists():
        return None

    runs = [d for d in checkpoint_base.iterdir() if d.is_dir()]
    if not runs:
        return None

    # Sort by name (which includes timestamp)
    latest = sorted(runs)[-1]
    return latest.name


def resume_from_checkpoint() -> Optional[CheckpointManager]:
    """
    Try to resume from the most recent checkpoint.

    Returns:
        CheckpointManager if checkpoints found, None otherwise
    """
    latest_run = find_latest_run()

    if not latest_run:
        print("📝 No previous checkpoints found")
        return None

    manager = CheckpointManager(run_id=latest_run)
    checkpoints = manager.list_checkpoints()

    if not checkpoints:
        print("📝 No valid checkpoints in latest run")
        return None

    print(f"🔄 Found checkpoints from run: {latest_run}")
    print("   Available stages:")
    for stage, info in checkpoints.items():
        timestamp = info.get('timestamp', 'unknown')
        print(f"      • {stage} ({timestamp})")

    return manager


if __name__ == "__main__":
    # Test checkpointing
    manager = CheckpointManager(run_id="test_run")

    # Save a test checkpoint
    test_data = pd.DataFrame({'a': [1, 2, 3], 'b': [4, 5, 6]})
    manager.save_checkpoint('test_stage', test_data, "Test checkpoint")

    # Load it back
    loaded = manager.load_checkpoint('test_stage')
    print("\nLoaded data:")
    print(loaded)

    # List checkpoints
    print("\nCheckpoints:")
    print(json.dumps(manager.list_checkpoints(), indent=2))

    # Clean up
    manager.clear_checkpoints()
