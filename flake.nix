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

        defaultPackage = with pkgs.poetry2nix; mkPoetryApplication {
            projectDir = ./.;
            preferWheels = true;
        };

        defaultApp = utils.lib.mkApp {
            drv = self.defaultPackage."${system}";
        };

    }; in with utils.lib; eachSystem defaultSystems out;

}
