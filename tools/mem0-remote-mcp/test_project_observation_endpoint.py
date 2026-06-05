import ast
from pathlib import Path


def test_server_exposes_project_observation_endpoint_without_raw_route_tokens():
    text = Path('tools/mem0-remote-mcp/server.py').read_text(encoding='utf-8')
    tree = ast.parse(text)
    routes = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute):
                for arg in decorator.args:
                    if isinstance(arg, ast.Constant):
                        routes.append((node.name, arg.value))

    assert ('handle_project_observation', '/project-observation/{token}') in routes
    assert 'ProjectObservationStore' in text
    assert 'PROJECT_OBSERVATIONS_LOG' in text


def test_server_exposes_progressive_project_retrieval_tools():
    tree = ast.parse(Path('tools/mem0-remote-mcp/server.py').read_text(encoding='utf-8'))
    tool_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)}

    assert {'project_search', 'project_timeline', 'project_fetch', 'project_file_context'} <= tool_names
