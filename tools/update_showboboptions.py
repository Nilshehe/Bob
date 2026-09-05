import re

def replace_function_content(filepath, function_name, new_function_body):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Find the function definition and replace its body
    # Pattern to match the function from definition to the end of the function
    pattern = rf'(def\s+{function_name}\s*$$[^$$]*$$:\s*\n)(.*?)(?=\n\S|\Z)'
    
    replacement = rf'\1{new_function_body}'
    
    new_content = re.sub(pattern, replacement, content, flags=re.DOTALL | re.MULTILINE)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(new_content)

# The new showBobOptions function that creates a circular layout
new_showBobOptions = '''    # Remove any existing options
    existing = document.getElementById("bob-options")
    if existing:
        existing.remove()

    # Create the options container
    container = document.createElement("div")
    container.id = "bob-options"
    container.style.position = "fixed"
    container.style.display = "flex"
    container.style.zIndex = "60"
    container.style.pointerEvents = "all"

    # Create the three oval options
    options = [
        { label: "Apps", tab: "apps" },
        { label: "Widgets", tab: "widgets" },
        { label: "Developer Mode", tab: "dev" }
    ]

    # Position options in a circle around the click point
    const radius = 80  // Distance from center
    const angleStep = (2 * Math.PI) / options.length  // Equal angular spacing

    options.forEach((opt, index) => {
        const angle = index * angleStep - Math.PI / 2  // Start from top
        const offsetX = radius * Math.cos(angle)
        const offsetY = radius * Math.sin(angle)

        const oval = document.createElement("div")
        oval.className = "bob-option"
        oval.textContent = opt.label
        oval.style.width = "70px"
        oval.style.height = "35px"
        oval.style.background = "rgba(0, 234, 255, 0.2)"
        oval.style.border = "1px solid rgba(0, 234, 255, 0.4)"
        oval.style.borderRadius = "50%"
        oval.style.display = "flex"
        oval.style.alignItems = "center"
        oval.style.justifyContent = "center"
        oval.style.cursor = "pointer"
        oval.style.transition = "background 0.2s ease, transform 0.1s ease"
        oval.style.position = "absolute"
        oval.style.left = (x + offsetX - 35) + "px"  // Adjust for half width
        oval.style.top = (y + offsetY - 17.5) + "px"   // Adjust for half height
        
        oval.onclick = () => {
            // Hide the options
            container.remove()
            // Switch to the tab
            bobMenuActiveTab = opt.tab
            loadBobMenuTab(opt.tab)
            // Open the menu and select the tab
            openBobMenu()
            // Ensure the tab is active
            bobMenuTabs.querySelectorAll("button").forEach(b => b.classList.toggle("active", b.dataset.tab === opt.tab))
        }
        oval.onmouseover = () => {
            oval.style.background = "rgba(0, 234, 255, 0.3)"
            oval.style.transform = "scale(1.05)"
        }
        oval.onmouseout = () => {
            oval.style.background = "rgba(0, 234, 255, 0.2)"
            oval.style.transform = "scale(1)"
        }
        container.appendChild(oval)
    })

    document.body.appendChild(container)

    // Hide options when clicking outside
    function hideOnOutsideClick(event) {
        if (!container.contains(event.target)) {
            container.remove()
            document.removeEventListener("click", hideOnOutsideClick)
        }
    }
    // Use setTimeout to avoid hiding on the same click
    setTimeout(() => {
        document.addEventListener("click", hideOnOutsideClick)
    }, 0)
'''

replace_function_content("gui/frontend/app.js", "showBobOptions", new_showBobOptions)
print("Updated showBobOptions function to create circular layout.")
