"""
Data Processor for Timepix Control System
Handles CLOG file parsing and particle counting
"""

import logging
import re
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Any
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class ClusterData:
    """Data for a single cluster (particle)"""
    x: int
    y: int
    energy: float
    toa: Optional[float] = None


@dataclass
class FrameData:
    """Data for a single frame"""
    frame_number: int
    frame_start: float
    frame_acq_time: float
    clusters: List[ClusterData]
    
    @property
    def particle_count(self) -> int:
        """Number of particles (clusters) in this frame"""
        return len(self.clusters)
    
    @property
    def total_energy(self) -> float:
        """Total energy deposited in this frame"""
        return sum(cluster.energy for cluster in self.clusters)
    
    @property
    def occupancy(self) -> float:
        """Frame occupancy (number of active pixels)"""
        # Each cluster contributes at least 1 pixel
        return float(self.particle_count)


class ClogParser:
    """Parser for CLOG format files"""
    
    # Regex patterns for CLOG parsing
    FRAME_PATTERN = re.compile(r'Frame\s+(\d+)\s+\(([0-9.]+),\s+([0-9.]+)\s+s\)')
    CLUSTER_PATTERN = re.compile(r'\[([0-9]+),\s*([0-9]+),\s*([0-9.]+)(?:,\s*([0-9.]+))?\]')
    
    @staticmethod
    def parse_file(filepath: str) -> List[FrameData]:
        """
        Parse a CLOG file and extract frame and cluster data
        
        Args:
            filepath: Path to the CLOG file
            
        Returns:
            List of FrameData objects
        """
        frames = []
        
        try:
            with open(filepath, 'r') as f:
                content = f.read()
            
            # Split into frame sections
            frame_sections = content.split('Frame ')
            
            for section in frame_sections[1:]:  # Skip first empty section
                try:
                    frame_data = ClogParser._parse_frame_section(section)
                    if frame_data:
                        frames.append(frame_data)
                except Exception as e:
                    logger.warning(f"Error parsing frame section: {e}")
            
            logger.info(f"Parsed {len(frames)} frames from {filepath}")
            return frames
            
        except FileNotFoundError:
            logger.error(f"CLOG file not found: {filepath}")
            return []
        except Exception as e:
            logger.error(f"Error parsing CLOG file {filepath}: {e}")
            return []
    
    @staticmethod
    def _parse_frame_section(section: str) -> Optional[FrameData]:
        """
        Parse a single frame section from CLOG file
        
        Args:
            section: Text section containing frame data
            
        Returns:
            FrameData object or None if parsing fails
        """
        lines = section.strip().split('\n')
        if not lines:
            return None
        
        # Parse frame header
        frame_match = ClogParser.FRAME_PATTERN.match(lines[0])
        if not frame_match:
            return None
        
        frame_number = int(frame_match.group(1))
        frame_start = float(frame_match.group(2))
        frame_acq_time = float(frame_match.group(3))
        
        # Parse clusters
        clusters = []
        for line in lines[1:]:
            line = line.strip()
            if not line:
                continue
            
            # Find all clusters in this line
            for match in ClogParser.CLUSTER_PATTERN.finditer(line):
                x = int(match.group(1))
                y = int(match.group(2))
                energy = float(match.group(3))
                toa = float(match.group(4)) if match.group(4) else None
                
                clusters.append(ClusterData(x=x, y=y, energy=energy, toa=toa))
        
        return FrameData(
            frame_number=frame_number,
            frame_start=frame_start,
            frame_acq_time=frame_acq_time,
            clusters=clusters
        )
    
    @staticmethod
    def get_frame_statistics(frame: FrameData) -> Dict[str, Any]:
        """
        Get statistical summary of a frame
        
        Args:
            frame: FrameData object
            
        Returns:
            Dictionary of statistics
        """
        if not frame.clusters:
            return {
                "particle_count": 0,
                "total_energy": 0.0,
                "avg_energy": 0.0,
                "min_energy": 0.0,
                "max_energy": 0.0,
                "occupancy": 0.0
            }
        
        energies = [c.energy for c in frame.clusters]
        
        return {
            "particle_count": frame.particle_count,
            "total_energy": frame.total_energy,
            "avg_energy": sum(energies) / len(energies),
            "min_energy": min(energies),
            "max_energy": max(energies),
            "occupancy": frame.occupancy
        }


class DataProcessor:
    """Process and analyze Timepix data files"""
    
    def __init__(self, data_directory: str = "data"):
        """
        Initialize data processor
        
        Args:
            data_directory: Base directory for data files
        """
        self.data_directory = Path(data_directory)
        self.parser = ClogParser()
    
    def process_clog_file(self, filepath: str) -> Dict[str, Any]:
        """
        Process a CLOG file and return summary statistics
        
        Args:
            filepath: Path to CLOG file
            
        Returns:
            Dictionary with processing results
        """
        frames = self.parser.parse_file(filepath)
        
        if not frames:
            return {
                "success": False,
                "error": "No frames found or parsing failed",
                "filepath": filepath
            }
        
        # Calculate overall statistics
        total_particles = sum(f.particle_count for f in frames)
        total_energy = sum(f.total_energy for f in frames)
        avg_particles_per_frame = total_particles / len(frames) if frames else 0
        
        frame_statistics = [self.parser.get_frame_statistics(f) for f in frames]
        
        return {
            "success": True,
            "filepath": filepath,
            "frame_count": len(frames),
            "total_particles": total_particles,
            "avg_particles_per_frame": avg_particles_per_frame,
            "total_energy": total_energy,
            "frames": frame_statistics
        }
    
    def process_session_directory(self, session_dir: str) -> Dict[str, Any]:
        """
        Process all CLOG files in a session directory
        
        Args:
            session_dir: Path to session directory
            
        Returns:
            Dictionary with processing results for all files
        """
        session_path = Path(session_dir)
        
        if not session_path.exists():
            logger.error(f"Session directory not found: {session_dir}")
            return {"success": False, "error": "Directory not found"}
        
        # Find all CLOG files
        clog_files = list(session_path.glob("*.clog"))
        
        if not clog_files:
            logger.warning(f"No CLOG files found in {session_dir}")
            return {"success": False, "error": "No CLOG files found"}
        
        logger.info(f"Processing {len(clog_files)} CLOG files from {session_dir}")
        
        results = {}
        for clog_file in clog_files:
            result = self.process_clog_file(str(clog_file))
            results[clog_file.name] = result
        
        # Calculate overall session statistics
        total_frames = sum(r.get("frame_count", 0) for r in results.values())
        total_particles = sum(r.get("total_particles", 0) for r in results.values())
        
        return {
            "success": True,
            "session_dir": session_dir,
            "file_count": len(clog_files),
            "total_frames": total_frames,
            "total_particles": total_particles,
            "files": results
        }
    
    def get_latest_session(self) -> Optional[str]:
        """
        Get the path to the most recent session directory
        
        Returns:
            Path to latest session or None
        """
        if not self.data_directory.exists():
            return None
        
        # Find all session directories
        session_dirs = [d for d in self.data_directory.iterdir() 
                       if d.is_dir() and d.name.startswith("session_")]
        
        if not session_dirs:
            return None
        
        # Sort by modification time
        latest_session = max(session_dirs, key=lambda d: d.stat().st_mtime)
        return str(latest_session)
    
    def monitor_file(self, filepath: str, callback) -> Tuple[int, float]:
        """
        Monitor a CLOG file for new frames and call callback with particle counts
        
        Args:
            filepath: Path to CLOG file to monitor
            callback: Function to call with (frame_number, particle_count)
            
        Returns:
            Tuple of (total_frames, total_particles)
        """
        last_size = 0
        total_frames = 0
        total_particles = 0
        
        try:
            file_path = Path(filepath)
            
            while file_path.exists():
                current_size = file_path.stat().st_size
                
                if current_size > last_size:
                    # File has grown, parse new content
                    frames = self.parser.parse_file(str(file_path))
                    
                    # Process new frames (those beyond what we've seen)
                    for frame in frames[total_frames:]:
                        particle_count = frame.particle_count
                        callback(frame.frame_number, particle_count)
                        total_particles += particle_count
                    
                    total_frames = len(frames)
                    last_size = current_size
                
                # Small delay before next check
                import time
                time.sleep(0.1)
            
        except Exception as e:
            logger.error(f"Error monitoring file {filepath}: {e}")
        
        return total_frames, total_particles
    
    def export_statistics_csv(self, session_dir: str, output_file: Optional[str] = None):
        """
        Export session statistics to CSV file
        
        Args:
            session_dir: Path to session directory
            output_file: Output CSV file path (None = auto-generate)
        """
        import csv
        
        results = self.process_session_directory(session_dir)
        
        if not results.get("success"):
            logger.error("Failed to process session directory")
            return
        
        if output_file is None:
            session_path = Path(session_dir)
            output_file = session_path / "statistics.csv"
        
        try:
            with open(output_file, 'w', newline='') as csvfile:
                writer = csv.writer(csvfile)
                
                # Write header
                writer.writerow([
                    "File",
                    "Frame Count",
                    "Total Particles",
                    "Avg Particles/Frame",
                    "Total Energy"
                ])
                
                # Write data for each file
                for filename, file_result in results.get("files", {}).items():
                    if file_result.get("success"):
                        writer.writerow([
                            filename,
                            file_result.get("frame_count", 0),
                            file_result.get("total_particles", 0),
                            f"{file_result.get('avg_particles_per_frame', 0):.2f}",
                            f"{file_result.get('total_energy', 0):.2f}"
                        ])
                
                # Write summary row
                writer.writerow([])
                writer.writerow([
                    "TOTAL",
                    results.get("total_frames", 0),
                    results.get("total_particles", 0),
                    "",
                    ""
                ])
            
            logger.info(f"Statistics exported to {output_file}")
            
        except Exception as e:
            logger.error(f"Error exporting statistics: {e}")
    
    def get_real_time_stats(self, filepath: str) -> Dict[str, Any]:
        """
        Get real-time statistics for the most recent frames in a CLOG file
        
        Args:
            filepath: Path to CLOG file
            
        Returns:
            Dictionary with latest statistics
        """
        frames = self.parser.parse_file(filepath)
        
        if not frames:
            return {
                "frame_count": 0,
                "latest_frame_particles": 0,
                "total_particles": 0,
                "avg_particles": 0.0
            }
        
        latest_frame = frames[-1]
        total_particles = sum(f.particle_count for f in frames)
        
        return {
            "frame_count": len(frames),
            "latest_frame_number": latest_frame.frame_number,
            "latest_frame_particles": latest_frame.particle_count,
            "total_particles": total_particles,
            "avg_particles": total_particles / len(frames)
        }
