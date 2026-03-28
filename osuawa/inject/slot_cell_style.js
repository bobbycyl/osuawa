(params) => {
    if (!params.value) return {};

    let rootMod = params.value.toString().substring(0, 2).toUpperCase();
    let colorMap = JSON.parse(`%s`);

    if (colorMap.hasOwnProperty(rootMod) && colorMap[rootMod]) {
        return {
            backgroundColor: colorMap[rootMod],
            color: "white",
            fontWeight: "bold",
        };
    } else {
        return {
            backgroundColor: "#eb50eb",
            color: "white",
            fontWeight: "bold",
        };
    }
};
