"""Composite HTML report that embeds the core charts."""

from pathlib import Path


def generate_master_report(fund_code: str, save_dir: Path) -> Path:
        """生成一个包含核心图表的综合 HTML 报告。"""
        charts = [
            f"{fund_code}_holdings_area.html",
            f"{fund_code}_nav_sma_boll.html",
            f"{fund_code}_risk_metrics.html"
        ]
        
        # 过滤出实际存在的文件，生成 iframe 代码
        iframes = ""
        for chart in charts:
            if (save_dir / chart).exists():
                iframes += f'<iframe src="{chart}" style="width:100%; height:750px; border:none; margin-bottom:20px; box-shadow:0 4px 12px rgba(0,0,0,0.1); border-radius:8px; background:white;"></iframe>\n'

        html_content = f"""<!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>{fund_code} · 综合分析报告</title>
            <style>
                body {{
                    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                    background-color: #f0f2f5;
                    margin: 0;
                    padding: 20px;
                }}
                .header {{
                    text-align: center;
                    margin-bottom: 30px;
                    color: #333;
                }}
                .container {{
                    max-width: 1200px;
                    margin: 0 auto;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>{fund_code} · 基金分析报告</h1>
                </div>
                {iframes}
            </div>
        </body>
        </html>"""

        out_path = save_dir / f"{fund_code}_master_report.html"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        
        return out_path
