allprojects {
    repositories {
        // Optional verified local mirror for development environments where
        // Java cannot reach the official Flutter Maven endpoint over TLS.
        val localFlutterMaven = rootProject.file("../.tools/flutter-maven")
        if (localFlutterMaven.isDirectory) {
            maven { url = uri(localFlutterMaven) }
        }
        google()
        mavenCentral()
    }
}

val newBuildDir: Directory =
    rootProject.layout.buildDirectory
        .dir("../../build")
        .get()
rootProject.layout.buildDirectory.value(newBuildDir)

subprojects {
    val newSubprojectBuildDir: Directory = newBuildDir.dir(project.name)
    project.layout.buildDirectory.value(newSubprojectBuildDir)
}
subprojects {
    project.evaluationDependsOn(":app")
}

tasks.register<Delete>("clean") {
    delete(rootProject.layout.buildDirectory)
}
