#!/usr/bin/env python3
"""
Unified Browser Automation Tool Selector

Automatically chooses the best browser tool for each task:
- Browserbase: Simple fetching, fastest
- Agent Browser: Complex automation, AI-driven
- GSD Browser: Local execution, free
- Playwright: Stealth mode, anti-detection
"""

import json
import subprocess
import sys
import os
import argparse
from typing import Dict, Any, List
from pathlib import Path

class BrowserAutomation:
    def __init__(self, config_path: str = None):
        self.workspace = Path.home() / ".openclaw" / "workspace"
        self.config_path = config_path or self.workspace / "skills" / "browser-automation" / "configs" / "browser-automation.json"
        self.config = self.load_config()
        
    def load_config(self) -> Dict[str, Any]:
        """Load configuration from JSON file"""
        try:
            with open(self.config_path) as f:
                return json.load(f)
        except FileNotFoundError:
            return self.get_default_config()
    
    def get_default_config(self) -> Dict[str, Any]:
        """Return default configuration"""
        return {
            "selection": {
                "defaultTool": "auto",
                "rules": {
                    "simple_fetch": "browserbase",
                    "complex_automation": "agent-browser", 
                    "stealth_required": "playwright",
                    "mobile_testing": "agent-browser",
                    "cost_sensitive": "gsd-browser"
                }
            }
        }
    
    def select_tool(self, task_type: str, instruction: str = "", **kwargs) -> str:
        """Select the optimal browser tool for the task"""
        
        # Force tool override
        if kwargs.get('force_tool'):
            return kwargs['force_tool']
        
        # Check for mobile requirement
        if kwargs.get('mobile') or 'mobile' in instruction.lower():
            return 'agent-browser'
            
        # Check for stealth requirement
        stealth_keywords = ['stealth', 'bot detection', 'anti-scraping', 'captcha']
        if kwargs.get('stealth') or any(kw in instruction.lower() for kw in stealth_keywords):
            return 'playwright'
            
        # Check for cost sensitivity
        if kwargs.get('local') or self.config['selection'].get('preferLocal', False):
            return 'gsd-browser'
            
        # Complex automation keywords
        complex_keywords = ['login', 'form', 'complex', 'multi-step', 'workflow', 'navigate']
        if any(kw in instruction.lower() for kw in complex_keywords):
            return 'agent-browser'
            
        # Simple fetch keywords  
        simple_keywords = ['fetch', 'scrape', 'extract', 'get', 'download']
        if any(kw in instruction.lower() for kw in simple_keywords):
            return 'browserbase'
            
        # Default fallback
        return self.config['selection']['rules'].get(task_type, 'browserbase')
    
    def execute_browserbase(self, url: str, **kwargs) -> Dict[str, Any]:
        """Execute using Browserbase API"""
        config = self.config['browserbase']
        
        # Build request
        data = {
            "url": url,
            "waitForTimeout": kwargs.get('wait', config['defaultTimeout'])
        }
        
        # Execute curl command
        cmd = [
            'curl', '-s', '-X', 'POST', config['endpoint'],
            '-H', f"x-bb-api-key: {config['apiKey']}", 
            '-H', 'Content-Type: application/json',
            '-d', json.dumps(data)
        ]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                response = json.loads(result.stdout)
                return {"success": True, "tool": "browserbase", "data": response}
            else:
                return {"success": False, "tool": "browserbase", "error": result.stderr}
        except Exception as e:
            return {"success": False, "tool": "browserbase", "error": str(e)}
    
    def execute_agent_browser(self, instruction: str, **kwargs) -> Dict[str, Any]:
        """Execute using Agent Browser"""
        
        # Check if installed
        if not self.check_tool_installed('agent-browser'):
            return {"success": False, "tool": "agent-browser", "error": "Not installed"}
        
        try:
            if kwargs.get('chat_mode'):
                # Natural language mode
                cmd = ['agent-browser', 'chat', instruction]
            else:
                # Direct command mode
                parts = instruction.split()
                cmd = ['agent-browser'] + parts
                
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            
            return {
                "success": result.returncode == 0,
                "tool": "agent-browser", 
                "data": result.stdout,
                "error": result.stderr if result.returncode != 0 else None
            }
        except Exception as e:
            return {"success": False, "tool": "agent-browser", "error": str(e)}
    
    def execute_gsd_browser(self, instruction: str, **kwargs) -> Dict[str, Any]:
        """Execute using GSD Browser"""
        config = self.config['gsdBrowser']
        
        # Check if binary exists
        if not os.path.exists(config['binaryPath']):
            return {"success": False, "tool": "gsd-browser", "error": "Binary not found"}
        
        try:
            # Parse instruction into GSD Browser commands
            if instruction.startswith('http'):
                # Simple navigation
                cmd = [config['binaryPath'], 'navigate', instruction]
            else:
                # Complex command
                cmd = [config['binaryPath']] + instruction.split()
                
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            
            return {
                "success": result.returncode == 0,
                "tool": "gsd-browser",
                "data": result.stdout,
                "error": result.stderr if result.returncode != 0 else None
            }
        except Exception as e:
            return {"success": False, "tool": "gsd-browser", "error": str(e)}
    
    def execute_playwright(self, instruction: str, **kwargs) -> Dict[str, Any]:
        """Execute using Playwright (via Python script)"""
        
        # Create temporary Python script
        script = f'''
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        # Add stealth if required
        if {kwargs.get('stealth', True)}:
            await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {{get: () => undefined}})")
        
        await page.goto("{instruction}")
        content = await page.content()
        await browser.close()
        print(content)

if __name__ == "__main__":
    asyncio.run(main())
'''
        
        try:
            # Execute via Python
            result = subprocess.run(
                ['python3', '-c', script], 
                capture_output=True, text=True, timeout=60
            )
            
            return {
                "success": result.returncode == 0,
                "tool": "playwright",
                "data": result.stdout,
                "error": result.stderr if result.returncode != 0 else None
            }
        except Exception as e:
            return {"success": False, "tool": "playwright", "error": str(e)}
    
    def check_tool_installed(self, tool: str) -> bool:
        """Check if a browser tool is installed"""
        checks = {
            'agent-browser': lambda: subprocess.run(['agent-browser', '--help'], capture_output=True).returncode == 0,
            'gsd-browser': lambda: os.path.exists(self.config['gsdBrowser']['binaryPath']),
            'playwright': lambda: subprocess.run(['python3', '-c', 'import playwright'], capture_output=True).returncode == 0,
            'browserbase': lambda: True  # API-only, no installation
        }
        
        try:
            return checks.get(tool, lambda: False)()
        except:
            return False
    
    def install_tool(self, tool: str) -> bool:
        """Install a browser tool"""
        installs = {
            'agent-browser': 'npm install -g agent-browser && agent-browser install',
            'playwright': 'pip3 install playwright playwright-stealth && playwright install chromium',
            'gsd-browser': 'curl -L -o gsd-browser https://github.com/gsd-build/gsd-browser/releases/download/v0.1.3/gsd-browser-linux-x64 && chmod +x gsd-browser'
        }
        
        if tool not in installs:
            return False
            
        try:
            result = subprocess.run(installs[tool], shell=True, capture_output=True)
            return result.returncode == 0
        except:
            return False
    
    def execute(self, task_type: str, instruction: str, **kwargs) -> Dict[str, Any]:
        """Main execution method with automatic tool selection and fallbacks"""
        
        # Select primary tool
        tool = self.select_tool(task_type, instruction, **kwargs)
        
        print(f"Selected tool: {tool} for task: {task_type}")
        
        # Try primary tool
        result = self._execute_with_tool(tool, instruction, **kwargs)
        
        # Try fallbacks if primary fails
        if not result['success'] and not kwargs.get('no_fallback'):
            fallbacks = self.config.get('fallbacks', {}).get(tool, [])
            
            for fallback_tool in fallbacks:
                print(f"Primary tool failed, trying fallback: {fallback_tool}")
                result = self._execute_with_tool(fallback_tool, instruction, **kwargs)
                if result['success']:
                    break
        
        return result
    
    def _execute_with_tool(self, tool: str, instruction: str, **kwargs) -> Dict[str, Any]:
        """Execute with a specific tool"""
        
        # Check if tool is installed
        if not self.check_tool_installed(tool):
            if kwargs.get('auto_install', True):
                print(f"Installing {tool}...")
                if not self.install_tool(tool):
                    return {"success": False, "tool": tool, "error": "Installation failed"}
            else:
                return {"success": False, "tool": tool, "error": "Tool not installed"}
        
        # Execute with selected tool
        if tool == 'browserbase':
            return self.execute_browserbase(instruction, **kwargs)
        elif tool == 'agent-browser':
            return self.execute_agent_browser(instruction, **kwargs)
        elif tool == 'gsd-browser':
            return self.execute_gsd_browser(instruction, **kwargs)
        elif tool == 'playwright':
            return self.execute_playwright(instruction, **kwargs)
        else:
            return {"success": False, "tool": tool, "error": "Unknown tool"}

def main():
    parser = argparse.ArgumentParser(description='Unified Browser Automation')
    parser.add_argument('task', choices=['fetch', 'complex', 'login', 'form', 'screenshot', 'monitor'])
    parser.add_argument('instruction', help='URL or instruction')
    parser.add_argument('--force-tool', choices=['browserbase', 'agent-browser', 'gsd-browser', 'playwright'])
    parser.add_argument('--mobile', action='store_true', help='Mobile testing mode')
    parser.add_argument('--stealth', action='store_true', help='Stealth mode')
    parser.add_argument('--local', action='store_true', help='Prefer local execution')
    parser.add_argument('--wait', type=int, default=3000, help='Wait time in milliseconds')
    parser.add_argument('--format', choices=['html', 'json'], default='html', help='Output format')
    parser.add_argument('--debug', action='store_true', help='Debug mode')
    
    args = parser.parse_args()
    
    # Create browser automation instance
    browser = BrowserAutomation()
    
    # Execute task
    result = browser.execute(
        task_type=args.task,
        instruction=args.instruction,
        force_tool=args.force_tool,
        mobile=args.mobile,
        stealth=args.stealth,
        local=args.local,
        wait=args.wait,
        debug=args.debug
    )
    
    # Output result
    if args.format == 'json':
        print(json.dumps(result, indent=2))
    else:
        if result['success']:
            print(f"✅ Success with {result['tool']}")
            print(result.get('data', ''))
        else:
            print(f"❌ Failed with {result['tool']}: {result.get('error', 'Unknown error')}")
            sys.exit(1)

if __name__ == '__main__':
    main()