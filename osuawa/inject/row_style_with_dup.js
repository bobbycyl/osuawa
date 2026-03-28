(params) => {
    if (params.data._is_dup_bid) return {backgroundColor: "crimson"};
    if (params.data._is_dup_song) return {backgroundColor: "lemonchiffon"};
    if (params.data.STATUS == 1) return {backgroundColor: "darkseagreen"};
    if (params.data.STATUS == 2) return {backgroundColor: "powderblue"};
    return {};
};
