class ImageLinkRenderer {
    init(params) {
        this.eGui = document.createElement("a");
        this.eGui.href = params.value;
        this.eGui.target = "_blank";

        const img = document.createElement("img");
        img.src = "%s" + params.data.BID + ".jpg";
        img.style.height = "32px";
        img.style.width = "70px";
        img.style.objectFit = "cover";
        img.style.borderRadius = "0px";

        this.eGui.appendChild(img);
    }

    getGui() {
        return this.eGui;
    }

    refresh(params) {
        return false;
    }
}
