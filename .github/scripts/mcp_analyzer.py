#!/usr/bin/env python3

import json
import sys
import subprocess
import re
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict
from pathlib import Path
import requests
from datetime import datetime

@dataclass
class CommitInfo:
    hash: str
    message: str
    author: str
    date: str
    files_changed: List[str]

@dataclass
class SubmoduleAnalysis:
    name: str
    path: str
    old_commit: str
    new_commit: str
    commits_count: int
    commits: List[CommitInfo]
    risk_level: str
    change_type: str
    breaking_changes: bool
    suggested_reviewers: List[str]

@dataclass
class AnalysisResult:
    summary: str
    submodules: List[SubmoduleAnalysis]
    total_commits: int
    overall_risk: str
    suggested_reviewers: List[str]
    pr_title: str
    pr_body: str

class SubmoduleAnalyzer:
    def __init__(self, config_path: str = "submodule_config.json"):
        """
        Inicializar el analizador con configuración de submódulos
        
        Ejemplo de submodule_config.json:
        {
            "submodules": {
                "frontend-lib": {
                    "reviewers": ["frontend-team", "user1"],
                    "critical": true,
                    "breaking_change_patterns": ["BREAKING", "breaking:", "!:"]
                },
                "backend-api": {
                    "reviewers": ["backend-team", "user2"],
                    "critical": true,
                    "breaking_change_patterns": ["BREAKING", "breaking:", "feat!:"]
                }
            },
            "default_reviewers": ["remr11"],
            "risk_keywords": {
                "high": ["security", "auth", "database", "migration"],
                "medium": ["api", "config", "deps", "dependency"],
                "low": ["docs", "test", "style", "format"]
            }
        }
        """
        self.config = self._load_config(config_path)
    
    def _load_config(self, config_path: str) -> Dict:
        """Cargar configuración de submódulos"""
        try:
            with open(config_path, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            # Configuración por defecto
            return {
                "submodules": {},
                "default_reviewers": ["remr11"],
                "risk_keywords": {
                    "high": ["security", "auth", "database", "migration", "breaking"],
                    "medium": ["api", "config", "deps", "dependency", "feature"],
                    "low": ["docs", "test", "style", "format", "refactor"]
                },
                "breaking_patterns": ["BREAKING", "breaking:", "!:", "feat!:"]
            }
    
    def _run_git_command(self, command: List[str], cwd: str = None) -> str:
        """Ejecutar comando git y retornar output"""
        if cwd and not Path(cwd).exists():
            return ""
            
        try:
            result = subprocess.run(
                command, 
                capture_output=True, 
                text=True, 
                cwd=cwd,
                check=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            print(f"Git command failed: {' '.join(command)}", file=sys.stderr)
            print(f"Error: {e.stderr}", file=sys.stderr)
            return ""
    
    def _get_commit_info(self, commit_hash: str, repo_path: str) -> CommitInfo:
        """Obtener información detallada de un commit"""
        # Obtener información básica del commit
        commit_header = self._run_git_command([
            'git', 'show', '--format=%H|%s|%an|%ai', '-s', commit_hash
        ], cwd=repo_path)
        
        # Obtener archivos cambiados por separado
        files_output = self._run_git_command([
            'git', 'show', '--name-only', '--format=', commit_hash
        ], cwd=repo_path)
        
        files_changed = [f for f in files_output.split('\n') if f.strip()] if files_output else []
        
        if not commit_header:
            return CommitInfo(commit_hash[:8], "Unknown", "Unknown", "", files_changed)
        
        header_parts = commit_header.split('|')
        if len(header_parts) >= 4:
            return CommitInfo(
                hash=header_parts[0][:8],
                message=header_parts[1],
                author=header_parts[2],
                date=header_parts[3],
                files_changed=files_changed
            )
        
        return CommitInfo(commit_hash[:8], "Unknown", "Unknown", "", files_changed)
    
    def _analyze_risk_level(self, commits: List[CommitInfo], submodule_name: str) -> tuple:
        """Analizar nivel de riesgo basado en commits y configuración"""
        risk_keywords = self.config.get("risk_keywords", {})
        submodule_config = self.config.get("submodules", {}).get(submodule_name, {})
        
        is_critical = submodule_config.get("critical", False)
        breaking_patterns = submodule_config.get("breaking_change_patterns", 
                                                self.config.get("breaking_patterns", ["BREAKING", "breaking:", "!:"]))
        
        high_risk_count = 0
        medium_risk_count = 0
        has_breaking_changes = False
        
        for commit in commits:
            message_lower = commit.message.lower()
            
            # Detectar breaking changes
            for pattern in breaking_patterns:
                if pattern.lower() in message_lower:
                    has_breaking_changes = True
                    high_risk_count += 1
                    break
            
            # Contar palabras clave de riesgo
            for keyword in risk_keywords.get("high", []):
                if keyword in message_lower:
                    high_risk_count += 1
                    break
            else:
                for keyword in risk_keywords.get("medium", []):
                    if keyword in message_lower:
                        medium_risk_count += 1
                        break
        
        # Determinar nivel de riesgo
        if has_breaking_changes or high_risk_count > 0:
            risk_level = "high"
        elif medium_risk_count > 2 or (medium_risk_count > 0 and is_critical):
            risk_level = "medium"
        else:
            risk_level = "low"
        
        # Determinar tipo de cambio
        if has_breaking_changes:
            change_type = "breaking"
        elif any("feat" in c.message.lower() for c in commits):
            change_type = "feature"
        elif any("fix" in c.message.lower() for c in commits):
            change_type = "bugfix"
        else:
            change_type = "maintenance"
        
        return risk_level, change_type, has_breaking_changes
    
    def _get_suggested_reviewers(self, submodule_analyses: List[SubmoduleAnalysis]) -> List[str]:
        """Obtener revisores sugeridos basado en submódulos modificados"""
        all_reviewers = set(self.config.get("default_reviewers", []))
        
        for analysis in submodule_analyses:
            submodule_config = self.config.get("submodules", {}).get(analysis.name, {})
            reviewers = submodule_config.get("reviewers", [])
            all_reviewers.update(reviewers)
        
        return list(all_reviewers)
    
    def _generate_pr_content(self, result: AnalysisResult) -> tuple:
        """Generar título y cuerpo del PR"""
        
        # Generar título
        if len(result.submodules) == 1:
            submodule = result.submodules[0]
            title = f"🤖 Update {submodule.name} ({submodule.commits_count} commits)"
            if submodule.breaking_changes:
                title = f"💥 {title} - BREAKING CHANGES"
        else:
            title = f"🤖 Update {len(result.submodules)} submodules ({result.total_commits} commits)"
            if any(s.breaking_changes for s in result.submodules):
                title = f"💥 {title} - BREAKING CHANGES"
        
        # Generar cuerpo del PR
        body_parts = []
        
        # Resumen ejecutivo
        body_parts.append(f"## 📊 Summary\n\n{result.summary}")
        
        # Detalles por submódulo
        body_parts.append("## 📦 Submodule Details\n")
        
        for submodule in result.submodules:
            risk_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}[submodule.risk_level]
            change_emoji = {
                "breaking": "💥",
                "feature": "✨", 
                "bugfix": "🐛",
                "maintenance": "🔧"
            }[submodule.change_type]
            
            body_parts.append(f"### {change_emoji} {submodule.name}")
            body_parts.append(f"- **Risk Level**: {risk_emoji} {submodule.risk_level.upper()}")
            body_parts.append(f"- **Change Type**: {submodule.change_type.title()}")
            body_parts.append(f"- **Commits**: {submodule.commits_count}")
            body_parts.append(f"- **From**: `{submodule.old_commit}` → **To**: `{submodule.new_commit}`")
            
            if submodule.breaking_changes:
                body_parts.append("- ⚠️  **CONTAINS BREAKING CHANGES**")
            
            # Commits destacados
            if submodule.commits:
                body_parts.append(f"\n**Recent commits**:")
                for commit in submodule.commits[:5]:  # Solo mostrar los últimos 5
                    body_parts.append(f"- `{commit.hash}` {commit.message} ({commit.author})")
                
                if len(submodule.commits) > 5:
                    body_parts.append(f"- ... and {len(submodule.commits) - 5} more commits")
            
            body_parts.append("")  # Línea vacía
        
        # Checklist de revisión
        body_parts.append("## ✅ Review Checklist\n")
        body_parts.append("- [ ] Verify all submodule updates are expected")
        body_parts.append("- [ ] Check for breaking changes impact")
        body_parts.append("- [ ] Ensure CI/CD compatibility")
        body_parts.append("- [ ] Update documentation if needed")
        
        if any(s.breaking_changes for s in result.submodules):
            body_parts.append("- [ ] 💥 **CRITICAL**: Review breaking changes carefully")
            body_parts.append("- [ ] Update dependent code if necessary")
        
        # Información del workflow
        body_parts.append("\n---")
        body_parts.append("*🤖 Generated automatically by submodule-bot with AI analysis*")
        
        return title, "\n".join(body_parts)
    
    def analyze_submodules(self, submodules_data: Dict) -> AnalysisResult:
        """Analizar cambios en submódulos y generar reporte completo"""
        
        submodule_analyses = []
        total_commits = 0
        
        for submodule_info in submodules_data.get("submodules", []):
            path = submodule_info["path"]
            new_commit = submodule_info["new_commit"]
            old_commit = submodule_info.get("old_commit", "")
            
            # Si no hay old_commit, intentar obtener el commit anterior
            if not old_commit or old_commit == "unknown":
                old_commit = self._run_git_command(['git', 'rev-parse', 'HEAD@{1}'], cwd=path)
                if not old_commit:
                    continue
            
            # Obtener nombre del submódulo
            submodule_name = Path(path).name
            
            # Obtener commits entre old y new
            commits = []
            commits_count = 0
            
            if old_commit and old_commit != new_commit:
                # Obtener lista de commits
                commit_range = f"{old_commit}..{new_commit}"
                commit_hashes = self._run_git_command([
                    'git', 'rev-list', '--reverse', commit_range
                ], cwd=path).split('\n')
                
                commits_count = len([c for c in commit_hashes if c.strip()])
                
                # Obtener información detallada de cada commit (máximo 10 para performance)
                for commit_hash in commit_hashes[:10]:
                    if commit_hash.strip():
                        commit_info = self._get_commit_info(commit_hash.strip(), path)
                        commits.append(commit_info)
            
            # Analizar riesgo
            risk_level, change_type, breaking_changes = self._analyze_risk_level(commits, submodule_name)
            
            # Obtener revisores sugeridos para este submódulo
            submodule_config = self.config.get("submodules", {}).get(submodule_name, {})
            suggested_reviewers = submodule_config.get("reviewers", [])
            
            analysis = SubmoduleAnalysis(
                name=submodule_name,
                path=path,
                old_commit=old_commit[:8] if old_commit else "unknown",
                new_commit=new_commit[:8],
                commits_count=commits_count,
                commits=commits,
                risk_level=risk_level,
                change_type=change_type,
                breaking_changes=breaking_changes,
                suggested_reviewers=suggested_reviewers
            )
            
            submodule_analyses.append(analysis)
            total_commits += commits_count
        
        # Calcular riesgo general
        if any(s.breaking_changes for s in submodule_analyses):
            overall_risk = "high"
        elif any(s.risk_level == "high" for s in submodule_analyses):
            overall_risk = "high"
        elif any(s.risk_level == "medium" for s in submodule_analyses):
            overall_risk = "medium"
        else:
            overall_risk = "low"
        
        # Generar resumen
        breaking_count = sum(1 for s in submodule_analyses if s.breaking_changes)
        if breaking_count > 0:
            summary = f"🚨 Updated {len(submodule_analyses)} submodule(s) with {total_commits} total commits. {breaking_count} submodule(s) contain BREAKING CHANGES that require careful review."
        else:
            summary = f"✅ Updated {len(submodule_analyses)} submodule(s) with {total_commits} total commits. No breaking changes detected."
        
        # Obtener todos los revisores sugeridos
        suggested_reviewers = self._get_suggested_reviewers(submodule_analyses)
        
        result = AnalysisResult(
            summary=summary,
            submodules=submodule_analyses,
            total_commits=total_commits,
            overall_risk=overall_risk,
            suggested_reviewers=suggested_reviewers,
            pr_title="",  # Se generará después
            pr_body=""    # Se generará después
        )
        
        # Generar contenido del PR
        title, body = self._generate_pr_content(result)
        result.pr_title = title
        result.pr_body = body
        
        return result

def main():
    """Función principal del MCP server"""
    if len(sys.argv) < 2:
        print("Usage: python mcp_analyzer.py <input_json_file>", file=sys.stderr)
        sys.exit(1)
    
    input_file = sys.argv[1]
    
    try:
        with open(input_file, 'r') as f:
            input_data = json.load(f)
        
        analyzer = SubmoduleAnalyzer()
        result = analyzer.analyze_submodules(input_data)
        
        # Convertir resultado a JSON y imprimir
        output = asdict(result)
        print(json.dumps(output, indent=2, ensure_ascii=False))
        
    except Exception as e:
        error_result = {
            "error": str(e),
            "summary": "Analysis failed",
            "submodules": [],
            "total_commits": 0,
            "overall_risk": "unknown",
            "suggested_reviewers": ["remr11"],
            "pr_title": "🤖 Submodule Update (Analysis Failed)",
            "pr_body": f"Submodule update completed but analysis failed: {str(e)}"
        }
        print(json.dumps(error_result, indent=2))
        sys.exit(1)

if __name__ == "__main__":
    main()
