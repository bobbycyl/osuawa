(event) => {
    let target = event.event.target;
    while (target && !target.classList.contains("ag-cell")) {
        target = target.parentElement;
    }

    if (target && event.value !== undefined && event.value !== null) {
        navigator.clipboard
            .writeText(String(event.value))
            .then(() => {
                const oldTip = target.querySelector(".copy-tip");
                if (oldTip) oldTip.remove();

                const tip = document.createElement("span");
                tip.className = "copy-tip";
                tip.innerText = "Copied";

                Object.assign(tip.style, {
                    position: "absolute",
                    left: "60%",
                    top: "50%",
                    transform: "translate(-50%, -50%)",
                    backgroundColor: "rgba(0, 0, 0, 0.75)",
                    color: "white",
                    fontSize: "9px",
                    padding: "1px 5px",
                    borderRadius: "2px",
                    zIndex: "1000",
                    pointerEvents: "none",
                    opacity: "0",
                    transition: "opacity 0.15s linear",
                });

                target.appendChild(tip);

                requestAnimationFrame(() => {
                    tip.style.opacity = "1";
                });

                setTimeout(() => {
                    tip.style.opacity = "0";
                    setTimeout(() => tip.remove(), 150);
                }, 300);
            })
            .catch((err) => {
                console.error("Failed to copy:", err);
            });
    }
};
