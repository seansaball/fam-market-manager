"""CSV and optional PDF export utilities."""

import csv
import os
from datetime import datetime

import pandas as pd


def export_dataframe_to_csv(df: pd.DataFrame, filepath: str) -> str:
    """Export a pandas DataFrame to CSV. Returns the full filepath."""
    df.to_csv(filepath, index=False)
    return filepath


def generate_export_filename(report_name: str, extension: str = "csv") -> str:
    """Generate a timestamped filename for an export."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = report_name.replace(" ", "_").lower()
    return f"fam_{safe_name}_{timestamp}.{extension}"


def export_vendor_reimbursement(data: list[dict], filepath: str) -> str:
    """Export vendor reimbursement report to CSV."""
    df = pd.DataFrame(data)
    return export_dataframe_to_csv(df, filepath)


def export_fam_match_report(data: list[dict], filepath: str) -> str:
    """Export FAM Match breakdown report to CSV."""
    df = pd.DataFrame(data)
    return export_dataframe_to_csv(df, filepath)


def export_detailed_ledger(data: list[dict], filepath: str) -> str:
    """Export detailed transaction ledger to CSV."""
    df = pd.DataFrame(data)
    return export_dataframe_to_csv(df, filepath)


def export_activity_log(data: list[dict], filepath: str) -> str:
    """Export full activity / audit log to CSV."""
    df = pd.DataFrame(data)
    return export_dataframe_to_csv(df, filepath)


def export_geolocation_report(data: list[dict], filepath: str) -> str:
    """Export geolocation zip code report to CSV."""
    df = pd.DataFrame(data)
    return export_dataframe_to_csv(df, filepath)
