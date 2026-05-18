import pandas as pd
import matplotlib.pyplot as plt
import os
import numpy as np

def generate_report():
    input_file = "comparison_results.csv"
    if not os.path.exists(input_file):
        print(f"Error: {input_file} not found. Please run the comparison script first.")
        return

    df = pd.read_csv(input_file)
    
    # Identify score columns (assume they end with '_score')
    score_cols = [col for col in df.columns if col.endswith('_score')]
    model_names = [col.replace('_score', '') for col in score_cols]
    
    # Clean data: Convert to numeric, replace non-numeric with NaN
    for col in score_cols:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    
    # Calculate Summary Statistics
    summary_stats = []
    data_to_plot = []
    
    # Calculate stats and store logic
    for model, col in zip(model_names, score_cols):
        clean_scores = df[col].dropna()
        if len(clean_scores) == 0:
            stats = {
                'Model': model,
                'Mean Score': 0, 'Median Score': 0, 
                'Max Score': 0, 'Min Score': 0, 'Std Dev': 0
            }
        else:
            stats = {
                'Model': model,
                'Mean Score': clean_scores.mean(),
                'Median Score': clean_scores.median(),
                'Max Score': clean_scores.max(),
                'Min Score': clean_scores.min(),
                'Std Dev': clean_scores.std()
            }
        summary_stats.append(stats)
    
    summary_df = pd.DataFrame(summary_stats)
    
    # Sort by Mean Score for chart (Ascending so best is at top in barh)
    summary_df_chart = summary_df.sort_values(by='Mean Score', ascending=True)

    print("Summary Statistics:")
    print(summary_df.sort_values(by='Mean Score', ascending=False))

    # 1. Visualization: Bar Chart of Mean Scores
    plt.figure(figsize=(10, len(model_names) * 0.5 + 2))
    plt.barh(summary_df_chart['Model'], summary_df_chart['Mean Score'], color='skyblue')
    plt.xlabel('Mean Reward Score')
    plt.title('Average Model Performance')
    plt.tight_layout()
    plt.savefig('mean_scores_chart.png')
    print("Generated mean_scores_chart.png")

    # 2. Visualization: Box Plot of Score Distributions
    # Use the same sorted order as the bar chart
    sorted_models = summary_df_chart['Model'].tolist()
    
    for model in sorted_models:
        col = f"{model}_score"
        vals = df[col].dropna().values
        data_to_plot.append(vals)
    
    plt.figure(figsize=(12, len(model_names) * 0.8 + 2))
    plt.boxplot(data_to_plot, vert=False, patch_artist=True, labels=sorted_models)
    plt.xlabel('Score Distribution')
    plt.title('Score Distribution by Model')
    plt.grid(axis='x', linestyle='--', alpha=0.7)
    plt.tight_layout()
    plt.savefig('score_distribution_chart.png')
    print("Generated score_distribution_chart.png")

    # 3. Generate HTML Report
    table_models = summary_df.sort_values(by='Mean Score', ascending=False)['Model'].tolist()
    
    html_content = f"""
    <html>
    <head>
        <title>Model Comparison Report</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; color: #333; }}
            h1, h2 {{ color: #2c3e50; border-bottom: 2px solid #eee; padding-bottom: 10px; }}
            table {{ border-collapse: collapse; width: 100%; margin-bottom: 20px; font-size: 14px; }}
            th, td {{ border: 1px solid #ddd; padding: 10px; text-align: left; }}
            th {{ background-color: #f8f9fa; color: #555; }}
            tr:nth-child(even) {{ background-color: #f9f9f9; }}
            tr:hover {{ background-color: #f1f1f1; }}
            .chart-container {{ display: flex; flex-wrap: wrap; gap: 40px; justify-content: center; margin: 40px 0; }}
            .chart-box {{ box-shadow: 0 4px 8px rgba(0,0,0,0.1); padding: 15px; border-radius: 8px; background: white; }}
            img {{ max-width: 100%; height: auto; }}
            .best-score {{ background-color: #d4edda; font-weight: bold; color: #155724; }}
        </style>
    </head>
    <body>
        <h1>Model Evaluation Report</h1>
        <p>Generated based on <code>comparison_results.csv</code></p>
        
        <h2>Summary Statistics</h2>
        <table>
            <tr>
                <th>Model</th>
                <th>Mean Score</th>
                <th>Median Score</th>
                <th>Max Score</th>
                <th>Min Score</th>
                <th>Std Dev</th>
            </tr>"""
    
    for _, row in summary_df.sort_values(by='Mean Score', ascending=False).iterrows():
        html_content += f"""
            <tr>
                <td>{row['Model']}</td>
                <td>{row['Mean Score']:.4f}</td>
                <td>{row['Median Score']:.4f}</td>
                <td>{row['Max Score']:.4f}</td>
                <td>{row['Min Score']:.4f}</td>
                <td>{row['Std Dev']:.4f}</td>
            </tr>"""
        
    html_content += """
        </table>
        
        <h2>Visualizations</h2>
        <div class="chart-container">
            <div class="chart-box">
                <h3>Average Scores</h3>
                <img src="mean_scores_chart.png" alt="Mean Scores Chart">
            </div>
            <div class="chart-box">
                <h3>Score Distributions</h3>
                <img src="score_distribution_chart.png" alt="Score Distribution Chart">
            </div>
        </div>

        <h2>Detailed Comparison (Cases)</h2>
        <p>This table shows the score for each test case. Cells with the highest score in a row are highlighted in green.</p>
        <table>
            <tr>
                <th>Project</th>
                <th>Test Name</th>
                <th>Bug ID</th>"""
    
    for model in table_models:
        html_content += f"<th>{model}</th>"
    
    html_content += "</tr>"
    
    # Limit to first 300 rows
    display_df = df.head(300)
    
    for idx, row in display_df.iterrows():
        html_content += "<tr>"
        html_content += f"<td>{row['project']}</td>"
        html_content += f"<td>{row['test_name']}</td>"
        html_content += f"<td>{row['bug_num']}</td>"
        
        # Calculate best score for this row
        row_numeric_scores = []
        for m in table_models:
             val = row.get(f"{m}_score")
             if pd.notna(val):
                 row_numeric_scores.append(val)
        
        max_score = max(row_numeric_scores) if row_numeric_scores else -float('inf')

        for model in table_models:
            score = row.get(f"{model}_score")
            assertion = row.get(f"{model}_assertion", "")
            
            # Format score
            score_display = "N/A" if pd.isna(score) else f"{score:.4f}"
            cell_class = ' class="best-score"' if (pd.notna(score) and score >= max_score) else ''
            
            # Escape HTML characters in assertion to prevent rendering issues
            import html
            assertion_safe = html.escape(str(assertion)) if pd.notna(assertion) else ""
            
            cell_content = f"<div><strong>{score_display}</strong></div>"
            if assertion_safe:
                cell_content += f"<div style='font-size:0.85em; color:#555; margin-top:4px; font-family:monospace; white-space: pre-wrap; word-break: break-all; max-width: 200px;'>{assertion_safe}</div>"
            
            html_content += f"<td{cell_class}>{cell_content}</td>"
            
        html_content += "</tr>"
        
    html_content += """
        </table>
    </body>
    </html>"""
    
    with open("comparison_report.html", "w") as f:
        f.write(html_content)
        
    print("Report generated: comparison_report.html")

if __name__ == "__main__":
    generate_report()
