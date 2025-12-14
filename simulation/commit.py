import os
import json

from dotenv import load_dotenv
from .utils import extract_hunks
from .partial_order import restore_edit_order
from .edit_dependency import analyze_dependency

current_path = os.path.abspath(os.path.dirname(__file__))
root_path = os.path.abspath(os.path.join(current_path, "../"))
load_dotenv(dotenv_path=os.path.join(root_path, ".env"))
FLOW_ANALYSIS_ENABLED = os.getenv("FLOW_ANALYSIS", "False").lower() in ("true", "1", "t", "y", "yes")
OUTPUT_DIR = os.getenv("OUTPUT_DIR")
os.makedirs(OUTPUT_DIR, exist_ok=True)

class Commit:
    def __init__(self, commit_url, repos_dir, system_under_test, logger):
        self.commit_sha = commit_url.split("/")[-1][:10]
        self.project_name = commit_url.split("/")[-3]
        self.repo_dir = os.path.join(repos_dir, self.project_name)
        self.commit_url = commit_url
        self.system_under_test = system_under_test
        self.logger = logger

        record_fp = os.path.join(OUTPUT_DIR, f"{self.project_name}-{self.commit_sha}-{self.system_under_test}-simulation-results.json")
        if os.path.exists(record_fp):
            with open(record_fp, "r") as f:
                record = json.load(f)
            self.commit_message = record["commit_message"]
            self.commit_snapshots = record["commit_snapshots"]
            if FLOW_ANALYSIS_ENABLED:
                assert "partial_orders" in record, f"Flow analysis enabled but no partial order records found for {self.commit_url}."
                self.logger.info(f"[FRAMEWORK] Flow analysis enabled.")
                self.partial_orders = record["partial_orders"]
            self.simulation_order = record["simulation_order"]
            self.replay_progress = [] # to track the progress of replaying this simulation progress
            self.SUT_prediction_records = record["SUT_prediction_records"]
            self.logger.info(f"[FRAMEWORK] Simulation results found for {self.commit_url} under {self.system_under_test}. Loaded records.")

        else:
            self.logger.info(f"[FRAMEWORK] No simulation results found for {self.commit_sha}. Extracting hunks and restoring edit order.")
            self.commit_message, self.commit_snapshots = extract_hunks(self.commit_url, repos_dir)
            if FLOW_ANALYSIS_ENABLED:
                self.logger.info(f"[FRAMEWORK] Flow analysis enabled. Recovering edit partial order graph for commit {self.commit_url}.")
                analyze_dependency(self, logger=self.logger)
                self.partial_orders, self.allowed_next_edit_idxs = restore_edit_order(self.commit_snapshots, commit_url, mock_order=False)
            
            self.allowed_next_edit_idxs = [0]
            self.simulation_order = []
            self.SUT_prediction_records = []

    def get_edit(self, idx):
        """
        Return edit hunk with specific index from the commit snapshot.
        """
        for file_path, file_snapshot in self.commit_snapshots.items():
            for window in file_snapshot:
                if isinstance(window, dict) and window["idx"] == idx:
                    return window
        
        raise ValueError(f"Edit with index {idx} not found in commit snapshot.")
    
    def get_edits(self):
        """
        Return all edits from the commit snapshot.
        """
        edits = []
        for file_path, file_snapshot in self.commit_snapshots.items():
            for window in file_snapshot:
                if isinstance(window, dict):
                    edits.append(window)
        return edits
    
    def update_edit_status(self, idx, status_name, status):
        """
        Update the status of an edit.
        """
        assert status_name in ["simulated", "allowed_as_next"], f"Invalid status name: {status_name}. Must be 'simulated' or 'allowed_as_next'."
        edit = self.get_edit(idx)
        edit[status_name] = status
        if status_name == "simulated" and status == True:
            self.simulation_order.append(idx)

    def update_allowed_as_next(self):
        """
        Update the allowed next edit status based on the last edit index.
        """
        edits = self.get_edits()
        simulated_edit_idxs = [edit["idx"] for edit in edits if edit["simulated"]]

        for edit in edits:
            if edit["idx"] in simulated_edit_idxs:
                self.update_edit_status(edit["idx"], "allowed_as_next", False)
            else:
                self.update_edit_status(edit["idx"], "allowed_as_next", True)

    def get_partial_order_graph(self):
        """
        Return the partial order graph of edits.
        """
        if not FLOW_ANALYSIS_ENABLED:
            self.logger.error(f"[FRAMEWORK] Flow analysis is disabled, no partial order graph available.")
            raise RuntimeError("Flow analysis is disabled.")
        return {
            "nodes": self.get_edits(),
            "edges": self.partial_orders
        }

    def simulation_status(self):
        """
        Print the simulation status of the commit.
        """
        edits = self.get_edits()
        allowed_next_edit_idxs = [edit["idx"] for edit in edits if edit["allowed_as_next"] == True]
        future_edit_idxs = [edit["idx"] for edit in edits if edit["simulated"] == False and edit["allowed_as_next"] == False]

        self.logger.info(f"[FRAMEWORK] Simulated edits:    {self.simulation_order}")
        self.logger.info(f"[FRAMEWORK] Allowed next edits: {allowed_next_edit_idxs}")
        self.logger.info(f"[FRAMEWORK] Future edits:       {future_edit_idxs}")

    def get_current_version(self):
        """
        Return the current version of the commit.
        """
        current_version = {}
        for file_path, file_snapshot in self.commit_snapshots.items():
            current_version[file_path] = []
            for window in file_snapshot:
                if isinstance(window, dict):
                    window["currently_start_at_line"] = len(current_version[file_path])
                    if window["simulated"]:
                        current_version[file_path].extend(window["after"])
                    else:
                        current_version[file_path].extend(window["before"])
                else:
                    current_version[file_path].extend(window)
        
        return current_version
    
    def get_prior_edits(self):
        """
        Return simulated edits as prior edits of this simulation.
        """
        edits = self.get_edits()
        prior_edits = []
        for simulated_edit_idx in self.simulation_order:
            prior_edits.append(edits[simulated_edit_idx])

        return prior_edits
    
    def get_not_simulated_edit_snapshots(self):
        """
        Return snapshot contains only the not simulated edits
        """
        allowed_next_edit_snapshots = {}
        for file_path, file_snapshot in self.commit_snapshots.items():
            allowed_next_edit_snapshots[file_path] = []
            for window in file_snapshot:
                if isinstance(window, dict) and not window["simulated"]:
                    allowed_next_edit_snapshots[file_path].append(window)
                else:
                    if isinstance(window, dict):
                        to_extend_window = window["after"].copy()
                    else:
                        to_extend_window = window.copy()

                    if len(allowed_next_edit_snapshots[file_path]) > 0 and isinstance(allowed_next_edit_snapshots[file_path][-1], list):
                        allowed_next_edit_snapshots[file_path][-1].extend(to_extend_window)
                    else:
                        allowed_next_edit_snapshots[file_path].append(to_extend_window)

        return allowed_next_edit_snapshots

    def save_simulation_results(self):
        with open(os.path.join(OUTPUT_DIR, f"{self.project_name}-{self.commit_sha}-{self.system_under_test}-simulation-results.json"), "w") as f:
            json.dump({
                "commit_sha": self.commit_sha,
                "project_name": self.project_name,
                "repo_dir": self.repo_dir,
                "commit_url": self.commit_url,
                "commit_message": self.commit_message,
                "commit_snapshots": self.commit_snapshots,
                "partial_orders": self.partial_orders if FLOW_ANALYSIS_ENABLED else None,
                "simulation_order": self.simulation_order,
                "SUT_prediction_records": self.SUT_prediction_records
            }, f, indent=4)

    def get_next_edit_snapshots(self, next_edit_idx):
        next_edit_snapshots = {}
        for file_path, snapshot in self.commit_snapshots.items():
            next_edit_snapshots[file_path] = []
            for window in snapshot:
                if isinstance(window, list):
                    if len(next_edit_snapshots[file_path]) == 0 or isinstance(next_edit_snapshots[file_path][-1], dict):
                        next_edit_snapshots[file_path].append(window.copy())
                    else:
                        next_edit_snapshots[file_path][-1].extend(window.copy())
                elif isinstance(window, dict) and window["idx"] != next_edit_idx:
                    if len(next_edit_snapshots[file_path]) == 0:
                        if window["simulated"]:
                            next_edit_snapshots[file_path].append(window["after"].copy())
                        else:
                            next_edit_snapshots[file_path].append(window["before"].copy())
                    else:
                        assert isinstance(next_edit_snapshots[file_path][-1], list)
                        if window["simulated"]:
                            next_edit_snapshots[file_path][-1].extend(window["after"].copy())
                        else:
                            next_edit_snapshots[file_path][-1].extend(window["before"].copy())
                elif isinstance(window, dict) and window["idx"] == next_edit_idx:
                    next_edit_snapshots[file_path].append(window.copy())

        return next_edit_snapshots

    def get_previously_applied_locations(self):
        """
        Return the locations of previously applied edits.
        """
        previously_applied_locations = {}
        for file_path, snapshot in self.commit_snapshots.items():
            line_idx = 0
            for window in snapshot:
                if isinstance(window, list):
                    line_idx += len(window)
                else:
                    if window["simulated"]:
                        previously_applied_locations[window["idx"]] = {
                            "idx": window["idx"],
                            "file_path": file_path,
                            "start_line": line_idx,
                            "atLines": [line_idx + i for i in range(len(window["after"]))]
                        }
                        line_idx += len(window["after"])
                    else:
                        line_idx += len(window["before"])

        return previously_applied_locations
