{
    inputs = {
        nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
        utils.url = "github:numtide/flake-utils";
    };

    outputs = {self, nixpkgs, utils}:
    let out = system:
    let pkgs = nixpkgs.legacyPackages."${system}";
    in {

        devShell = pkgs.mkShell {
            buildInputs = with pkgs; [
                python3Packages.poetry-core
                python3Packages.requests
                python3Packages.pytz
                python3Packages.oauthlib
                python3Packages.beautifulsoup4
                jq
            ];
        };

        defaultPackage = let
            pythonEnv = pkgs.python3.withPackages (ps: with ps; [
                requests
                pytz
                oauthlib
                beautifulsoup4
            ]);
        in pkgs.stdenv.mkDerivation {
            pname = "a2councilbot";
            version = "0.1.0";
            src = ./.;
            nativeBuildInputs = [ pkgs.makeWrapper ];

            installPhase = ''
                mkdir -p $out/bin $out/lib/a2councilbot
                cp *.py $out/lib/a2councilbot/
                for script in council_twitter_bot find_events get_cm_voting_record get_masto_creds get_twitter_creds make_csv make_repo; do
                    makeWrapper ${pythonEnv}/bin/python3 $out/bin/$script \
                        --add-flags "$out/lib/a2councilbot/$script.py" \
                        --prefix PYTHONPATH : "$out/lib/a2councilbot"
                done
            '';

            meta.mainProgram = "council_twitter_bot";
        };

        defaultApp = utils.lib.mkApp {
            drv = self.defaultPackage."${system}";
        };

    }; in with utils.lib; eachSystem defaultSystems out;

}
