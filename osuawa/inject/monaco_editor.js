class MonacoEditorCell {
    constructor() {
        this.modal = null;
        this.editor = null;
        this.keyHandler = null;
        this.clickHandler = null;
        this.isClosing = false; // 防止重复关闭
    }

    init(params) {
        this.params = params;
        this.value = params.value || "";
        this.language = params.language || "json";
        this.eGui = this.createPreview();
        this.editorTitle =
            params.columnName +
            " of (" +
            params.data.BID +
            ", " +
            params.data.MODS +
            ")";
    }

    createPreview() {
        const container = document.createElement("div");
        container.style.cssText = `
            width: 100%; height: 100%; cursor: pointer;
            display: flex; align-items: center;
        `;

        const preview = document.createElement("div");
        preview.style.cssText = `
            width: 100%; 
            min-height: 60px;
            max-height: 200px; 
            padding: 8px 12px;
            font-family: monospace; 
            font-size: 12px;
            line-height: 1.2;
            background: #f5f5f5; 
            border: 1px solid #ddd;
            overflow: auto;
            word-wrap: break-word;
            word-break: break-all;
            white-space: pre-wrap;
        `;
        preview.textContent = this.value || "Click to edit.";

        // 悬停效果
        preview.onmouseenter = () => {
            preview.style.background = "#e8e8e8";
            preview.style.borderColor = "#999";
        };
        preview.onmouseleave = () => {
            preview.style.background = "#f5f5f5";
            preview.style.borderColor = "#ddd";
        };

        this.clickHandler = (e) => {
            e.stopPropagation(); // 防止触发 aggrid 的编辑
            this.openEditor();
        };
        preview.addEventListener("click", this.clickHandler);

        this.preview = preview;
        container.appendChild(preview);
        return container;
    }

    async openEditor() {
        if (this.modal || this.isClosing) return;

        try {
            if (!window.monaco) await this.loadMonaco();
            this.createModal();
            this.initMonaco();
            this.setupEventListeners();
        } catch (err) {
            console.error("Failed to load Monaco Editor:", err);
        }
    }

    loadMonaco() {
        return new Promise((resolve, reject) => {
            if (window.monaco) {
                resolve();
                return;
            }

            // 避免重复加载
            if (window.__monacoLoading) {
                const check = setInterval(() => {
                    if (window.monaco) {
                        clearInterval(check);
                        resolve();
                    }
                }, 100);
                return;
            }

            window.__monacoLoading = true;

            const script = document.createElement("script");
            script.src =
                "https://unpkg.com/monaco-editor@0.45.0/min/vs/loader.js";
            script.onload = () => {
                require.config({
                    paths: {
                        vs: "https://unpkg.com/monaco-editor@0.45.0/min/vs",
                    },
                });
                require(["vs/editor/editor.main"], () => {
                    window.__monacoLoading = false;
                    resolve();
                }, (err) => {
                    window.__monacoLoading = false;
                    reject(err);
                });
            };
            script.onerror = () => {
                window.__monacoLoading = false;
                reject(new Error("Failed to load Monaco"));
            };
            document.head.appendChild(script);
        });
    }

    createModal() {
        const overlay = document.createElement("div");
        overlay.className = "monaco-editor-overlay";
        overlay.style.cssText = `
            position: fixed; top: 0; left: 0;
            width: 100%; height: 100%;
            background: rgba(0, 0, 0, 0.6); z-index: 2147483647;
            display: flex; align-items: center; justify-content: center;
            backdrop-filter: blur(2px);
        `;

        const dialog = document.createElement("div");
        dialog.style.cssText = `
            width: 90%; height: 85%; max-width: 1200px;
            background: #1e1e1e; border-radius: 12px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);
            display: flex; flex-direction: column; overflow: hidden;
        `;

        // 标题栏 - 使用独立ID避免冲突
        const uid = Date.now() + "_" + Math.random().toString(36).substr(2, 9);
        const titleBar = document.createElement("div");
        titleBar.style.cssText = `
            padding: 12px 20px; background: #2d2d2d;
            border-bottom: 1px solid #3c3c3c;
            display: flex; justify-content: space-between;
            align-items: center; user-select: none;
        `;
        titleBar.innerHTML = `
            <span style="color: #fff; font-size: 14px; font-weight: 500;">
                ✏️ ${this.editorTitle}
            </span>
            <div style="display: flex; gap: 8px;">
                <button id="monaco-save-${uid}" style="
                    background: transparent;
                    border: none;
                    color: rgba(255, 255, 255, 0.65);
                    padding: 6px 16px;
                    border-radius: 6px;
                    cursor: pointer;
                    font-size: 13px;
                    font-weight: 500;
                    border: 0.5px solid rgba(48, 48, 48, 0);
                " onmouseover="this.style.border='0.5px solid rgba(127,127,127,0.4)'; this.style.color='rgba(255,255,255,0.9)'" 
                onmouseout="this.style.border='0.5px solid rgba(48,48,48,0)'; this.style.color='rgba(255,255,255,0.65)'">Save (Ctrl+S)</button>
                
                <button id="monaco-cancel-${uid}" style="
                    background: transparent;
                    border: none;
                    color: rgba(255, 255, 255, 0.65);
                    padding: 6px 16px;
                    border-radius: 6px;
                    cursor: pointer;
                    font-size: 13px;
                    font-weight: 500;
                    border: 0.5px solid rgba(48, 48, 48, 0);
                " onmouseover="this.style.border='0.5px solid rgba(240,98,161,0.4)'; this.style.color='rgba(255,255,255,0.9)'" 
                onmouseout="this.style.border='0.5px solid rgba(48,48,48,0)'; this.style.color='rgba(255,255,255,0.65)'">Close (Esc)</button>
            </div>
        `;

        this.editorContainer = document.createElement("div");
        this.editorContainer.style.cssText = `
            flex: 1; position: relative; overflow: hidden;
        `;

        dialog.appendChild(titleBar);
        dialog.appendChild(this.editorContainer);
        overlay.appendChild(dialog);
        document.body.appendChild(overlay);

        this.modal = {
            overlay,
            dialog,
            uid,
            saveBtn: document.getElementById(`monaco-save-${uid}`),
            cancelBtn: document.getElementById(`monaco-cancel-${uid}`),
        };

        // 点击遮罩关闭
        this.overlayClickHandler = (e) => {
            if (e.target === overlay) this.closeEditor();
        };
        overlay.addEventListener("click", this.overlayClickHandler);
    }

    initMonaco() {
        this.editor = monaco.editor.create(this.editorContainer, {
            value: this.value,
            language: this.language,
            theme: "vs-dark",
            automaticLayout: true,
            fontSize: 13,
            fontFamily: 'Consolas, "Courier New", monospace',
            lineNumbers: "on",
            minimap: {enabled: false},
            scrollBeyondLastLine: false,
            wordWrap: "on",
            tabSize: 2,
            insertSpaces: true,
            formatOnPaste: false,
            formatOnType: false,
            renderWhitespace: "boundary",
            contextmenu: true,
            mouseWheelZoom: false,
        });

        this.editor.focus();
    }

    setupEventListeners() {
        const handleSave = () => {
            if (this.isClosing) return;
            this.value = this.editor.getValue();
            this.preview.textContent = this.value || "Click to edit.";
            this.params.setValue(this.value);
            this.closeEditor();
            this.params.stopEditing();
        };

        const handleCancel = () => {
            if (this.isClosing) return;
            this.closeEditor();
            this.params.stopEditing();
        };

        // 绑定按钮
        if (this.modal.saveBtn) {
            this.saveBtnHandler = handleSave;
            this.modal.saveBtn.onclick = this.saveBtnHandler;
        }
        if (this.modal.cancelBtn) {
            this.cancelBtnHandler = handleCancel;
            this.modal.cancelBtn.onclick = this.cancelBtnHandler;
        }

        // 键盘事件 - 使用捕获阶段但检查目标
        this.keyHandler = (e) => {
            // 只在编辑器激活时处理
            if (!this.editor || this.isClosing) return;

            // 检查焦点是否在编辑器内
            const isEditorFocused =
                this.editor.hasTextFocus() ||
                this.editorContainer.contains(document.activeElement);

            if (
                !isEditorFocused &&
                document.activeElement !== this.modal.saveBtn &&
                document.activeElement !== this.modal.cancelBtn
            )
                return;

            if (e.key === "Escape") {
                e.preventDefault();
                e.stopPropagation();
                handleCancel();
            } else if (
                (e.ctrlKey || e.metaKey) &&
                e.key.toLowerCase() === "s"
            ) {
                e.preventDefault();
                e.stopPropagation();
                handleSave();
            }
        };

        document.addEventListener("keydown", this.keyHandler, true);

        // 处理窗口大小变化
        this.resizeHandler = () => {
            if (this.editor) this.editor.layout();
        };
        window.addEventListener("resize", this.resizeHandler);
    }

    closeEditor() {
        if (!this.modal || this.isClosing) return;
        this.isClosing = true;

        // 移除所有事件监听
        if (this.keyHandler) {
            document.removeEventListener("keydown", this.keyHandler, true);
            this.keyHandler = null;
        }

        if (this.resizeHandler) {
            window.removeEventListener("resize", this.resizeHandler);
            this.resizeHandler = null;
        }

        if (this.modal.overlay && this.overlayClickHandler) {
            this.modal.overlay.removeEventListener(
                "click",
                this.overlayClickHandler,
            );
        }

        // 销毁编辑器
        if (this.editor) {
            try {
                this.editor.dispose();
            } catch (e) {
                console.warn("销毁编辑器时出错:", e);
            }
            this.editor = null;
        }

        // 移除DOM
        if (this.modal.overlay && this.modal.overlay.parentNode) {
            this.modal.overlay.parentNode.removeChild(this.modal.overlay);
        }

        this.modal = null;
        this.editorContainer = null;
        this.isClosing = false;
    }

    getGui() {
        return this.eGui;
    }

    getValue() {
        return this.value;
    }

    isPopup() {
        return true;
    }

    // AG Grid 销毁回调
    destroy() {
        this.closeEditor();
        if (this.preview && this.clickHandler) {
            this.preview.removeEventListener("click", this.clickHandler);
        }
        if (this.eGui) {
            this.eGui.innerHTML = "";
        }
    }
}
